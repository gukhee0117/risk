from datetime import date

import httpx
import pytest

from app.clients.kis import KisClient
from app.domain import RiskError


def row(day, price="100", volume="1"):
    return {"stck_bsop_date": day, "stck_clpr": price, "acml_vol": volume}


def client_for(handler):
    return KisClient("TEST_KEY", "TEST_SECRET", transport=httpx.MockTransport(handler), sleep=lambda _: None)


def test_pagination_adjusted_option_and_token_reuse():
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json={"access_token": "TEST_TOKEN"})
        assert request.url.params["FID_ORG_ADJ_PRC"] == "0"
        assert request.url.params["FID_COND_MRKT_DIV_CODE"] == "J"
        end = request.url.params["FID_INPUT_DATE_2"]
        rows = [row("20240104"), row("20240103")] if end == "20240104" else [row("20240102"), row("20240101")]
        return httpx.Response(200, json={"rt_cd": "0", "output1": {"stck_shrn_iscd": "005930"}, "output2": rows})

    client = client_for(handler)
    try:
        for _ in range(2):
            prices = client.history("005930", date(2024, 1, 1), date(2024, 1, 4))
            assert [p.day.day for p in prices] == [1, 2, 3, 4]
        assert sum(r.url.path == "/oauth2/tokenP" for r in requests) == 1
        assert len(requests) == 5
    finally:
        client.close()


@pytest.mark.parametrize("rows", [
    [row("20240101"), row("20240101")], [row("20240105")],
    [row("20240101", "0")], [row("20240101", "NaN")],
    [row("20240101", volume="-1")], [row("bad-date")],
    [row("20240101"), row("20240103"), row("20240102")],
])
def test_invalid_provider_data(rows):
    def handler(request):
        if request.url.path == "/oauth2/tokenP":
            return httpx.Response(200, json={"access_token": "TEST_TOKEN"})
        return httpx.Response(200, json={"rt_cd": "0", "output1": {}, "output2": rows})
    client = client_for(handler)
    try:
        with pytest.raises(RiskError):
            client.history("005930", date(2024, 1, 1), date(2024, 1, 4))
    finally:
        client.close()


def test_rate_limit_retries_are_bounded_and_sanitized():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, text="TEST_SECRET TEST_KEY")

    client = client_for(handler)
    try:
        with pytest.raises(RiskError) as failure:
            client.history("005930", date(2024, 1, 1), date(2024, 1, 4))
        assert len(calls) == 3
        assert "TEST_SECRET" not in str(failure.value)
        assert "TEST_KEY" not in str(failure.value)
    finally:
        client.close()


def test_provider_error_never_uses_payload_as_error_message():
    def handler(request):
        return httpx.Response(200, json={"error_description": "TEST_SECRET"})
    client = client_for(handler)
    try:
        with pytest.raises(RiskError) as failure:
            client.history("005930", date(2024, 1, 1), date(2024, 1, 4))
        assert failure.value.code == "KIS_AUTH_FAILED"
        assert "TEST_SECRET" not in str(failure.value)
    finally:
        client.close()
