"""Local integration checks and an opt-in, generation-free API check."""

import os
from types import SimpleNamespace
from unittest.mock import patch

from openai import OpenAI
import pytest

from providers import deepseek


def test_missing_key_fails_before_connecting(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with patch.object(deepseek, "OpenAI") as client:
        with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
            deepseek.run_deepseek("Local test")
    client.assert_not_called()


def test_provider_configuration_and_response_without_network(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "unit-test-key")
    with patch.object(deepseek, "OpenAI") as client:
        completion = client.return_value.chat.completions.create
        completion.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Local response"))]
        )

        assert deepseek.run_deepseek("Local test") == "Local response"

        client.assert_called_once_with(
            api_key="unit-test-key", base_url=deepseek.DEEPSEEK_BASE_URL,
        )
        completion.assert_called_once()
        request = completion.call_args.kwargs
        assert request["model"] == deepseek.DEEPSEEK_MODEL
        assert request["messages"][0]["role"] == "system"
        assert request["messages"][1] == {"role": "user", "content": "Local test"}
        assert request["stream"] is False


@pytest.mark.skipif(
    os.environ.get("DEEPSEEK_LIVE_TEST") != "1",
    reason="Set DEEPSEEK_LIVE_TEST=1 to check the real API without generating tokens.",
)
def test_live_authentication_and_model_without_generation():
    # Importing the provider loads config.py and the repository's .env.
    if not os.environ.get("DEEPSEEK_API_KEY"):
        pytest.fail("DEEPSEEK_API_KEY is not configured.", pytrace=False)

    try:
        with OpenAI(
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url=deepseek.DEEPSEEK_BASE_URL,
            timeout=15,
            max_retries=0,
        ) as client:
            # GET /models only: no prompts or generation requests are sent.
            models = client.models.list()
    except Exception as error:
        # Report enough to diagnose connectivity/auth without exposing credentials.
        status = getattr(error, "status_code", None)
        pytest.fail(
            f"DeepSeek connection failed: {type(error).__name__} (HTTP {status}).",
            pytrace=False,
        )

    assert deepseek.DEEPSEEK_MODEL in {model.id for model in models.data}, (
        f"Configured model is unavailable: {deepseek.DEEPSEEK_MODEL}"
    )
