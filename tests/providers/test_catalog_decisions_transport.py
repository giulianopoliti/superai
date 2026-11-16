import json
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError

import pytest

from app.providers.catalog_decisions import OpenAICatalogDecisions


def test_transient_service_error_has_bounded_retry_without_exposing_key():
    provider = OpenAICatalogDecisions("secret-test")
    payload = json.dumps(
        {"model": "gpt-6-luna", "answers": [], "usage": {"input_tokens": 1}}
    ).encode()
    with (
        patch(
            "app.providers.catalog_decisions.urlopen",
            side_effect=[
                HTTPError("https://api.openai.com/v1/decisions", 503, "Unavailable", {}, None),
                BytesIO(payload),
            ],
        ) as request,
        patch("app.providers.catalog_decisions.time.sleep") as sleep,
    ):
        raw, _ = provider.decide({"original_name": "CAFE"}, [])
    assert request.call_count == 2 and sleep.call_count == 1
    assert raw["transport_attempts"] == 2


def test_authentication_error_is_not_retried():
    provider = OpenAICatalogDecisions("secret-test")
    with patch(
        "app.providers.catalog_decisions.urlopen",
        side_effect=HTTPError("https://api.openai.com/v1/decisions", 401, "Unauthorized", {}, None),
    ) as request:
        with pytest.raises(RuntimeError, match="HTTP 401") as error:
            provider.decide({"original_name": "CAFE"}, [])
    assert request.call_count == 1 and "secret-test" not in str(error.value)
