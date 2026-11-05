from dataclasses import replace

from app.services.preflight import preflight


def metadata_check(result):
    return next(row for row in result['checks'] if row['name']=='grading_metadata')


def test_new_etf_without_asset_grade_is_blocked(market,tmp_path):
    settings=market[-1]
    path=tmp_path/'new-etf.csv'
    path.write_text('ticker,name,status,kind,inception_date,leveraged_or_inverse\n'
                    '069500,synthetic,active,ETF,2026-01-01,false\n',encoding='utf-8')
    result=preflight(replace(settings,universe=path),market[1])
    assert metadata_check(result)['invalidTickers']==['069500']
    assert metadata_check(result)['status']=='FAIL'


def test_unknown_age_requires_history_without_fabricated_inception(market,tmp_path):
    settings=market[-1]
    path=tmp_path/'etf.csv'
    path.write_text('ticker,name,status,kind,leveraged_or_inverse\n'
                    '069500,synthetic,active,ETF,false\n',encoding='utf-8')
    result=preflight(replace(settings,universe=path),market[1])
    assert metadata_check(result)['code']=='FUND_AGE_REQUIRES_HISTORY'


def test_missing_kind_is_detected_before_batch(market,tmp_path):
    path=tmp_path/'old.csv'
    path.write_text('ticker,name,status\n005930,synthetic,active\n',encoding='utf-8')
    result=preflight(replace(market[-1],universe=path),market[1])
    assert metadata_check(result)['invalidTickers']==['005930']
