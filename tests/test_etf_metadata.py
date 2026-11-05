from datetime import date

import httpx
import pytest

from app.clients.kis import KisClient
from app.domain import RiskError


def metadata_client(row):
    def handler(request):
        if request.url.path=='/oauth2/tokenP':return httpx.Response(200,json={'access_token':'synthetic'})
        assert request.url.path=='/uapi/etfetn/v1/quotations/inquire-price'
        assert request.headers['tr_id']=='FHPST02400000'
        assert request.url.params['FID_INPUT_ISCD']=='069500'
        return httpx.Response(200,json={'rt_cd':'0','output':row})
    return KisClient('synthetic','synthetic',transport=httpx.MockTransport(handler))


@pytest.mark.parametrize('multiple,elevated',[('1.00',False),('2.00',True),('-1.00',True),('-2.00',True)])
def test_tracking_multiplier_is_used_instead_of_product_name(multiple,elevated):
    client=metadata_client({'etf_trc_ert_mltp':multiple,'stck_lstn_date':'20021014'})
    try:
        result=client.etf_metadata('069500')
        assert result['leveraged_or_inverse'] is elevated
        assert result['listed_on']==date(2002,10,14)
    finally:client.close()


@pytest.mark.parametrize('row',[{}, {'etf_trc_ert_mltp':'NaN','stck_lstn_date':'20021014'},
    {'etf_trc_ert_mltp':'0','stck_lstn_date':'20021014'},
    {'etf_trc_ert_mltp':'1','stck_lstn_date':'invalid'}])
def test_unknown_structure_or_date_is_not_silently_accepted(row):
    client=metadata_client(row)
    try:
        with pytest.raises(RiskError) as error:client.etf_metadata('069500')
        assert error.value.code=='GRADING_METADATA_REQUIRED'
    finally:client.close()
