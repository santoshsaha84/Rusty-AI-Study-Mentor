"""
LLM client abstraction — routes to Ollama (local dev) or Vertex AI (production).
Output is always validated by Pydantic before use — never eval()d.
"""
import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import get_settings


@dataclass
class LLMResult:
    data: dict
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency_ms: float
    json_valid: bool


async def generate_structured(
    messages: list[dict],
    system_instruction: str,
    response_schema: dict,
    max_output_tokens: int = 2048,
) -> LLMResult:
    settings = get_settings()
    if settings.llm_provider == "ollama":
        return await _ollama_generate(messages, system_instruction, response_schema, max_output_tokens)
    return await _vertex_generate(messages, system_instruction, response_schema, max_output_tokens)


async def _ollama_generate(
    messages: list[dict],
    system_instruction: str,
    response_schema: dict,
    max_output_tokens: int,
) -> LLMResult:
    settings = get_settings()

    ollama_messages = [{"role": "system", "content": system_instruction}]
    for m in messages:
        role = m["role"]
        content = " ".join(p["text"] for p in m["parts"])
        if role == "model":
            role = "assistant"
        ollama_messages.append({"role": role, "content": content})

    payload = {
        "model": settings.ollama_model,
        "messages": ollama_messages,
        "stream": False,
        # Structured output: constrains generation to the response schema so the
        # model can't return bare/empty JSON objects.
        "format": response_schema,
        "options": {
            "temperature": 0.1,
            "top_p": 0.9,
            "repeat_penalty": 1.1,
            "num_predict": max_output_tokens,
            # Ollama defaults to a 4096-token window, which RAG prompts (~3.5k tokens)
            # plus the answer overflow — the prompt gets truncated and output is cut short.
            "num_ctx": settings.ollama_num_ctx,
        },
    }

    start = time.perf_counter()
    async with httpx.AsyncClient(timeout=settings.ollama_timeout_s) as client:
        resp = await client.post(f"{settings.ollama_base_url}/api/chat", json=payload)
        resp.raise_for_status()
    latency_ms = (time.perf_counter() - start) * 1000

    body = resp.json()
    raw_text = body["message"]["content"]

    prompt_tokens = body.get("prompt_eval_count", 0)
    completion_tokens = body.get("eval_count", 0)

    json_valid = True
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        json_valid = False
        raise ValueError(f"LLM returned invalid JSON: {exc}\nRaw: {raw_text[:500]}") from exc

    return LLMResult(
        data=data,
        provider="ollama",
        model=settings.ollama_model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        latency_ms=round(latency_ms, 1),
        json_valid=json_valid,
    )


async def _vertex_generate(
    messages: list[dict],
    system_instruction: str,
    response_schema: dict,
    max_output_tokens: int,
) -> LLMResult:
    from google.genai import types

    settings = get_settings()
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        response_mime_type="application/json",
        response_json_schema=response_schema,
        max_output_tokens=max_output_tokens,
        temperature=0.1,
        top_p=0.9,
    )

    contents = [
        types.Content(role=m["role"], parts=[types.Part.from_text(text=p["text"]) for p in m["parts"]])
        for m in messages
    ]

    start = time.perf_counter()
    response = await _genai_client().aio.models.generate_content(
        model=settings.gemini_model,
        contents=contents,
        config=config,
    )
    latency_ms = (time.perf_counter() - start) * 1000

    usage = response.usage_metadata
    prompt_tokens = (usage.prompt_token_count or 0) if usage else 0
    completion_tokens = (usage.candidates_token_count or 0) if usage else 0

    raw = response.text or ""
    json_valid = True
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        json_valid = False
        raise ValueError(f"Gemini returned invalid JSON: {exc}") from exc

    return LLMResult(
        data=data,
        provider="vertex",
        model=settings.gemini_model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        latency_ms=round(latency_ms, 1),
        json_valid=json_valid,
    )


async def embed_text(text: str) -> list[float]:
    """Embed a student query (retrieval-side)."""
    settings = get_settings()
    if settings.llm_provider == "ollama":
        return await _ollama_embed(text)
    return await _vertex_embed(text, task_type="RETRIEVAL_QUERY")


async def embed_batch(texts: list[str]) -> list[list[float]]:
    """Embed textbook chunks (ingestion-side)."""
    settings = get_settings()
    if settings.llm_provider == "ollama":
        return [await _ollama_embed(t) for t in texts]
    return await _vertex_embed_batch(texts)


async def _ollama_embed(text: str) -> list[float]:
    settings = get_settings()
    payload = {
        "model": settings.ollama_embed_model,
        "prompt": text,
    }
    # Ollama serves one request at a time, so an embedding can queue behind a
    # multi-minute test generation — use the same generous timeout as chat.
    async with httpx.AsyncClient(timeout=settings.ollama_timeout_s) as client:
        resp = await client.post(f"{settings.ollama_base_url}/api/embeddings", json=payload)
        resp.raise_for_status()
    return resp.json()["embedding"]


_genai: Any = None


def _genai_client() -> Any:
    """Lazily-created Google Gen AI client.

    GEMINI_AUTH=adc     -> Vertex AI / Gemini Enterprise endpoint with Application Default
                           Credentials (the Cloud Run service account; no key to manage).
    GEMINI_AUTH=api_key -> Gemini API with GEMINI_API_KEY (from Secret Manager).
    Configured explicitly from Settings so the SDK never picks up stray GOOGLE_* env vars.
    """
    global _genai
    if _genai is None:
        from google import genai

        settings = get_settings()
        if settings.gemini_auth == "api_key":
            _genai = genai.Client(
                enterprise=False,
                api_key=settings.gemini_api_key.get_secret_value(),
            )
        else:
            _genai = genai.Client(
                enterprise=True,
                project=settings.vertex_ai_project,
                location=settings.vertex_ai_location,
            )
    return _genai


# gemini-embedding-001 accepts one input per request on Vertex AI — fan out with bounded concurrency.
_EMBED_CONCURRENCY = 8


async def _vertex_embed(text: str, task_type: str) -> list[float]:
    from google.genai import types

    settings = get_settings()
    result = await _genai_client().aio.models.embed_content(
        model=settings.vertex_embed_model,
        contents=text,
        config=types.EmbedContentConfig(
            task_type=task_type,
            output_dimensionality=settings.embed_dim,
        ),
    )
    values = result.embeddings[0].values
    if len(values) != settings.embed_dim:
        raise ValueError(
            f"Embedding has {len(values)} dims, expected {settings.embed_dim} "
            f"(model={settings.vertex_embed_model})"
        )
    return values


async def _vertex_embed_batch(texts: list[str]) -> list[list[float]]:
    import asyncio

    sem = asyncio.Semaphore(_EMBED_CONCURRENCY)

    async def _one(t: str) -> list[float]:
        async with sem:
            return await _vertex_embed(t, task_type="RETRIEVAL_DOCUMENT")

    return list(await asyncio.gather(*(_one(t) for t in texts)))
