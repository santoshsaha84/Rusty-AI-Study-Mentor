"""Tests for the Vertex AI (google-genai) path of the LLM client abstraction."""
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from rag.retrieval import llm_client


def _settings(**overrides):
    base = dict(
        llm_provider="vertex",
        gemini_model="gemini-3.1-flash-lite",
        vertex_embed_model="gemini-embedding-001",
        embed_dim=1024,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _mock_client(generate_response=None, embed_values=None):
    client = MagicMock()
    client.aio.models.generate_content = AsyncMock(return_value=generate_response)
    client.aio.models.embed_content = AsyncMock(
        return_value=SimpleNamespace(embeddings=[SimpleNamespace(values=embed_values)])
    )
    return client


@pytest.mark.asyncio
async def test_vertex_generate_returns_parsed_json():
    response = SimpleNamespace(
        text='{"key_points": ["a"], "notes": "n", "misconceptions": [], "has_math": false}',
        usage_metadata=SimpleNamespace(prompt_token_count=12, candidates_token_count=8),
    )
    client = _mock_client(generate_response=response)

    with patch.object(llm_client, "get_settings", return_value=_settings()), \
         patch.object(llm_client, "_genai_client", return_value=client):
        result = await llm_client.generate_structured(
            [{"role": "user", "parts": [{"text": "What is a square?"}]}],
            "system prompt",
            {"type": "object", "properties": {"notes": {"type": "string"}}},
        )

    assert result.provider == "vertex"
    assert result.model == "gemini-3.1-flash-lite"
    assert result.data["notes"] == "n"
    assert result.total_tokens == 20
    config = client.aio.models.generate_content.call_args.kwargs["config"]
    assert config.response_mime_type == "application/json"
    assert config.response_json_schema == {"type": "object", "properties": {"notes": {"type": "string"}}}


@pytest.mark.asyncio
async def test_vertex_generate_rejects_invalid_json():
    response = SimpleNamespace(text="not json", usage_metadata=None)
    client = _mock_client(generate_response=response)

    with patch.object(llm_client, "get_settings", return_value=_settings()), \
         patch.object(llm_client, "_genai_client", return_value=client):
        with pytest.raises(ValueError):
            await llm_client.generate_structured(
                [{"role": "user", "parts": [{"text": "q"}]}], "sys", {"type": "object"}
            )


@pytest.mark.asyncio
async def test_embed_text_uses_query_task_type_and_1024_dims():
    client = _mock_client(embed_values=[0.1] * 1024)

    with patch.object(llm_client, "get_settings", return_value=_settings()), \
         patch.object(llm_client, "_genai_client", return_value=client):
        vec = await llm_client.embed_text("What is a square?")

    assert len(vec) == 1024
    config = client.aio.models.embed_content.call_args.kwargs["config"]
    assert config.task_type == "RETRIEVAL_QUERY"
    assert config.output_dimensionality == 1024


@pytest.mark.asyncio
async def test_embed_batch_uses_document_task_type():
    client = _mock_client(embed_values=[0.2] * 1024)

    with patch.object(llm_client, "get_settings", return_value=_settings()), \
         patch.object(llm_client, "_genai_client", return_value=client):
        vecs = await llm_client.embed_batch(["chunk one", "chunk two", "chunk three"])

    assert len(vecs) == 3
    assert client.aio.models.embed_content.await_count == 3
    for call in client.aio.models.embed_content.call_args_list:
        assert call.kwargs["config"].task_type == "RETRIEVAL_DOCUMENT"


@pytest.mark.asyncio
async def test_embed_wrong_dimension_raises():
    client = _mock_client(embed_values=[0.1] * 3072)

    with patch.object(llm_client, "get_settings", return_value=_settings()), \
         patch.object(llm_client, "_genai_client", return_value=client):
        with pytest.raises(ValueError, match="3072 dims"):
            await llm_client.embed_text("q")
