from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.domain import RiskError
from app.main import create_app


def classification_app(market, tmp_path, *, elevated=False, failure=None):
    _, now, _, _, _, settings = market
    universe = tmp_path / 'stocks.csv'
    universe.write_text('ticker,name,status,kind,leveraged_or_inverse\n'
        '005930,stock,active,STOCK,\n'
        '069500,etf,active,ETF,false\n'
        '091990,delisted,stale,STOCK,\n', encoding='utf-8')
    # Grade-only classification must not require a price DB or calendar.
    settings = replace(settings, universe=universe, database=tmp_path/'absent.sqlite3', calendar=None)
    calls = []
    class Client:
        def __init__(self, *_): calls.append('created')
        def stock_elevated(self, ticker):
            calls.append(ticker)
            if failure: raise failure
            return elevated
        def close(self): calls.append('closed')
    return create_app(settings, clock=lambda: now, classification_client_factory=Client), calls, now


@pytest.mark.parametrize('elevated,grade', [(False,2),(True,1)])
def test_live_classification_is_independent_of_metrics_storage(market,tmp_path,elevated,grade):
    api,calls,now=classification_app(market,tmp_path,elevated=elevated)
    with TestClient(api) as client:
        response=client.get('/stocks/005930/classification',headers={'X-Risk-Service-Key':'synthetic-test-service-key'})
    assert response.status_code==200
    assert response.headers['cache-control']=='no-store'
    assert response.json()=={'ticker':'005930','riskGrade':grade,'asOf':now.isoformat(),
                            'basis':'STOCK_RULE_CURRENT_STATUS'}
    assert calls==['created','005930','closed']


def test_authentication_precedes_provider_access(market,tmp_path):
    api,calls,_=classification_app(market,tmp_path)
    with TestClient(api) as client:
        assert client.get('/stocks/005930/classification').status_code==401
    assert calls==[]


@pytest.mark.parametrize('ticker',['069500','091990','999999','5930'])
def test_nonstock_disabled_unknown_and_invalid_codes_do_not_call_kis(market,tmp_path,ticker):
    api,calls,_=classification_app(market,tmp_path)
    with TestClient(api) as client:
        response=client.get(f'/stocks/{ticker}/classification',headers={'X-Risk-Service-Key':'synthetic-test-service-key'})
    assert response.status_code in {400,422}
    assert calls==[]


def test_provider_failure_never_falls_back_to_normal_grade(market,tmp_path):
    api,calls,_=classification_app(market,tmp_path,failure=RiskError('KIS_UNAVAILABLE','Unavailable',503))
    with TestClient(api) as client:
        response=client.get('/stocks/005930/classification',headers={'X-Risk-Service-Key':'synthetic-test-service-key'})
    assert response.status_code==503
    assert 'riskGrade' not in response.json()
    assert calls[-1]=='closed'


def test_unknown_classification_never_becomes_standard_stock(market,tmp_path):
    api,calls,_=classification_app(market,tmp_path,elevated=None)
    with TestClient(api) as client:
        response=client.get('/stocks/005930/classification',headers={'X-Risk-Service-Key':'synthetic-test-service-key'})
    assert response.status_code==503
    assert calls[-1]=='closed'
