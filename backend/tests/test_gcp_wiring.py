"""Tests for GCP-specific wiring: Gemini client auth, rate-limit keys, readiness, stream output scan."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr
from starlette.requests import Request

from rag.retrieval import llm_client


def _gemini_settings(**overrides):
    base = dict(
        gemini_auth="adc", gemini_api_key=SecretStr(""),
        vertex_ai_project="rusty-staging-khel", vertex_ai_location="global",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def fresh_genai(monkeypatch):
    monkeypatch.setattr(llm_client, "_genai", None)


def test_genai_client_adc_uses_enterprise_endpoint(fresh_genai):
    with patch.object(llm_client, "get_settings", return_value=_gemini_settings()), \
         patch("google.genai.Client") as client_cls:
        llm_client._genai_client()
    client_cls.assert_called_once_with(enterprise=True, project="rusty-staging-khel", location="global")


def test_genai_client_api_key_mode(fresh_genai):
    settings = _gemini_settings(gemini_auth="api_key", gemini_api_key=SecretStr("key-123"))
    with patch.object(llm_client, "get_settings", return_value=settings), \
         patch("google.genai.Client") as client_cls:
        llm_client._genai_client()
    client_cls.assert_called_once_with(enterprise=False, api_key="key-123")


def _request(headers: dict) -> Request:
    return Request({
        "type": "http", "method": "GET", "path": "/", "client": ("203.0.113.9", 1234),
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
    })


def test_rate_limit_key_per_token_not_per_ip():
    from app.core.limiter import rate_limit_key

    a = rate_limit_key(_request({"Authorization": "Bearer token-a"}))
    b = rate_limit_key(_request({"Authorization": "Bearer token-b"}))
    assert a != b and a.startswith("tok:")
    assert "token-a" not in a
    assert rate_limit_key(_request({})) == "203.0.113.9"


@pytest.mark.asyncio
async def test_ready_returns_503_when_database_down():
    from app.api.health import ready

    failing_factory = MagicMock(side_effect=ConnectionError("down"))
    with patch("app.api.health.get_session_factory", return_value=failing_factory), \
         patch("app.api.health.get_settings") as mock_settings:
        mock_settings.return_value.llm_provider = "vertex"
        mock_settings.return_value.gemini_model = "gemini-3.1-flash-lite"
        resp = await ready()

    assert resp.status_code == 503
    assert json.loads(resp.body)["checks"]["database"].startswith("error")


def test_study_output_scan_covers_all_fields():
    from app.services import retrieval

    with patch.object(retrieval, "tier1_scan", side_effect=lambda t: "BAD" in t):
        assert retrieval.study_output_blocked({"key_points": [], "notes": "", "misconceptions": ["BAD"]})
        assert retrieval.study_output_blocked({"key_points": ["BAD"], "notes": "", "misconceptions": []})
        assert not retrieval.study_output_blocked({"key_points": ["ok"], "notes": "fine", "misconceptions": []})


@pytest.mark.asyncio
async def test_streaming_cache_hit_is_output_scanned():
    from app.services import retrieval

    cached = SimpleNamespace(
        prompt_version=retrieval.PROMPT_VERSION,
        response_json={"key_points": ["BAD"], "notes": "", "misconceptions": []},
    )
    cache_repo = MagicMock()
    cache_repo.find_similar = AsyncMock(return_value=cached)
    service = retrieval.RetrievalService(MagicMock(), db_session=AsyncMock())

    with patch.object(retrieval, "embed_text", AsyncMock(return_value=[0.0] * 1024)), \
         patch.object(retrieval, "CacheRepository", return_value=cache_repo), \
         patch.object(retrieval, "tier1_scan", side_effect=lambda t: "BAD" in t), \
         patch.object(retrieval.RAGTrace, "persist", AsyncMock()):
        events = [e async for e in service.retrieve_streaming("q", 8, "mathematics", None, [])]

    joined = "".join(events)
    assert "event: error" in joined
    assert "event: result" not in joined
