"""Opt-in local Docker verification using synthetic prices and isolated volumes.

No KIS calls, production secrets or host workspace mounts. Deletes only resources
created here after checking an unpredictable ownership label.
"""

import argparse
import json
import os
import secrets
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    owner=uuid4().hex
    container='risk-check-'+owner
    volume=container+'-data'
    env={**os.environ,'RISK_SERVICE_KEY':secrets.token_urlsafe(32)}
    headers={'X-Risk-Service-Key':env['RISK_SERVICE_KEY']}
    created_container=created_volume=False
    checks=[]

    def command(*parts, input=None):
        return subprocess.run(['docker',*parts],input=input,text=True,capture_output=True,
                              check=True,env=env,timeout=60).stdout.strip()

    def owned(kind, name):
        return command(kind,'inspect','--format','{{index .Labels "risk.validation"}}' if kind=='volume'
                       else '{{index .Config.Labels "risk.validation"}}',name) == owner

    def address():
        port=json.loads(command('inspect','--format','{{json .NetworkSettings.Ports}}',container))['8000/tcp'][0]['HostPort']
        return 'http://127.0.0.1:'+port

    def start():
        command('run','--detach','--name',container,'--label','risk.validation='+owner,
                '--mount','type=volume,src='+volume+',dst=/data','--publish','127.0.0.1::8000',
                '--env','RISK_SERVICE_KEY','--env','RISK_SCHEDULER_ENABLED=false',args.image)
        return address()

    def ready(base):
        for _ in range(60):
            try:
                response=httpx.get(base+'/health',timeout=1,trust_env=False)
                if response.status_code == 200:
                    return response.json()
            except httpx.TransportError:
                pass
            time.sleep(.2)
        state=command('inspect','--format','{{.State.Status}}',container)
        raise AssertionError('Container did not become responsive; state='+state)

    try:
        command('volume','create','--label','risk.validation='+owner,volume)
        created_volume=True
        base=start()
        created_container=True
        assert ready(base)['lastSuccessfulCalculationAt'] is None
        with httpx.Client(base_url=base,timeout=3,trust_env=False) as api:
            assert api.get('/grades').status_code == 401
            assert api.get('/grades',headers={'X-Risk-Service-Key':'wrong'}).status_code == 401
            assert api.get('/grades',headers=headers).status_code == 503
        checks.append('initial health and authentication')
        code="""
import os,json
from decimal import Decimal
from pathlib import Path
from app.config import Settings
from app.domain import Price,now_kst
from app.repositories.sqlite import Repository
from app.services.calendar import TradingCalendar
from app.services.batch import run_batch
from app.services.backup import daily_backup
from app.universe import load_universe
s=Settings.from_env()
assert os.getuid()==10001 and not Path('/app/.env').exists() and not Path('/app/docs').exists()
c=TradingCalendar.from_csv(s.calendar,s.trading_hours)
t=c.latest_batch_target(now_kst())
sessions=c.window(t)
amount=Decimal(100000)
prices=[]
for i,d in enumerate(sessions):
 if i: amount*=Decimal('1.01') if i%2 else Decimal('0.98')
 prices.append(Price(d,amount,100))
class Client:
 def history(self,*_): return prices
r=Repository(s.database)
result=run_batch(r,c,{'005930':load_universe(s.universe)['005930']},Client(),now_kst(),target_date=t)
assert result['updated']==['005930']
daily_backup(r,t)
with r.snapshot() as db:
 assert db.execute('pragma journal_mode').fetchone()[0]=='wal'
 assert db.execute('pragma integrity_check').fetchone()[0]=='ok'
print(json.dumps({'uid':os.getuid(),'version':1,'priceTo':str(t)}))
"""
        info=json.loads(command('exec','-i',container,'python','-B','-',input=code))
        checks.append('non-root volume writes, synthetic batch, WAL, backup, integrity')
        with httpx.Client(base_url=base,headers=headers,timeout=3,trust_env=False) as api:
            rows=api.get('/grades').json()
            assert len(rows)==1 and rows[0]['analysisVersion']==1
            response=api.post('/package/volatility',json={'items':[{'ticker':'005930','weight':1}]})
            assert response.status_code==200 and response.json()['volatility']==rows[0]['volatility']
        checks.append('real HTTP grade and package contract')
        # Fake credentials exercise configuration checks only. Preflight never
        # constructs a KIS client and must leave source data/versions unchanged.
        report = json.loads(command('exec', '-e', 'KIS_APP_KEY=synthetic-preflight-key',
                                    '-e', 'KIS_APP_SECRET=synthetic-preflight-secret',
                                    container, 'python', '-m', 'app.cli', 'preflight', '--probe-storage'))
        by_name = {row['name']: row for row in report['checks']}
        assert report['status'] != 'BLOCKED' and report['deploymentApproved'] is False
        assert all(by_name[name]['status'] == 'PASS' for name in
                   ('database', 'backup', 'database_directory', 'backup_directory'))
        assert 'synthetic-preflight-key' not in json.dumps(report)
        assert httpx.get(base+'/grades',headers=headers,timeout=3,trust_env=False).json()==rows
        checks.append('preflight JSON, non-root WAL probes, backup check, no source changes')
        # An independent Linux process owns the same batch lock.
        hold="from app.config import Settings; from app.services.batch import batch_lock; import sys\nwith batch_lock(Settings.from_env().database.with_suffix('.batch.lock')):\n print('READY',flush=True)\n sys.stdin.read()"
        process=subprocess.Popen(['docker','exec','-i',container,'python','-u','-c',hold],
                                 stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                assert executor.submit(process.stdout.readline).result(timeout=10).strip()=='READY'
            assert httpx.get(base+'/health',timeout=3,trust_env=False).json()['batch_running'] is True
        finally:
            process.stdin.close()
            process.wait(timeout=10)
            process.stdout.close()
            process.stderr.close()
        assert ready(base)['batch_running'] is False
        checks.append('cross-process Linux batch_running')
        command('exec',container,'python','-m','app.cli','backup','/data/manual-snapshot.sqlite3')
        command('restart',container)
        base=address()  # Docker may assign a different ephemeral host port.
        ready(base)
        assert httpx.get(base+'/grades',headers=headers,timeout=3,trust_env=False).json()==rows
        assert owned('container',container)
        command('rm','--force',container)
        created_container=False
        base=start()
        created_container=True
        ready(base)
        assert httpx.get(base+'/grades',headers=headers,timeout=3,trust_env=False).json()==rows
        checks.append('backup CLI and named-volume restart/recreation preservation')
        result={'image':args.image,'synthetic':True,'checks':checks,'container':info,'kis_calls':0}
        args.report.parent.mkdir(parents=True,exist_ok=True)
        args.report.write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(result))
    finally:
        if created_container and owned('container',container):
            command('rm','--force',container)
        if created_volume and owned('volume',volume):
            command('volume','rm',volume)


if __name__=='__main__':
    main()
