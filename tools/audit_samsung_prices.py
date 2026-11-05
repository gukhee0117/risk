"""Read-only cross-check of stored Samsung return-tail observations."""
import json
import sqlite3
from datetime import date
from urllib.parse import unquote

import httpx
import numpy as np

from app.config import ROOT, read_env
from tools.compare_price_basis import ENDPOINTS, public_row


def main():
    with sqlite3.connect((ROOT / 'data/risk.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        rows = db.execute("SELECT trade_date,close_price FROM price_daily WHERE ticker='005930' ORDER BY trade_date").fetchall()
    prices = np.array([float(row[1]) for row in rows])
    returns = prices[1:] / prices[:-1] - 1
    indices = np.argsort(returns)[:3]
    days = sorted({rows[j][0] for i in indices for j in (i, i + 1)})
    stored = dict(rows)
    key = read_env(ROOT / '.env', allowed={'DATA_GO_KR_SERVICE_KEY'}).get('DATA_GO_KR_SERVICE_KEY')
    if not key:
        print('PUBLIC_KEY_MISSING')
        return 1
    result = {'ticker': '005930', 'quantile': float(np.percentile(returns, 2.5)), 'records': []}
    try:
        with httpx.Client(timeout=30) as client:
            for day in days:
                response = client.get(ENDPOINTS['stock'], params={'serviceKey': unquote(key), 'resultType': 'json',
                    'numOfRows': 10, 'pageNo': 1, 'basDt': day.replace('-', ''), 'likeSrtnCd': '005930'})
                response.raise_for_status()
                row = public_row(response.json(), '005930', date.fromisoformat(day))
                result['records'].append({'date': day, 'stored_close': stored[day], 'public': row})
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        print('PUBLIC_CHECK_FAILED_NO_CREDENTIALS_LOGGED')
        return 1
    output = ROOT / 'data/samsung-tail-audit-20261105.json'
    with output.open('x', encoding='utf-8') as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
