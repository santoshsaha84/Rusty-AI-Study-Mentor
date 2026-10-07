"""
RetrievalService — the only interface between FastAPI routes and the RAG stack.
Routes never touch pgvector, LLMs, or embeddings directly.
"""
import asyncio
import json
import logging
import random
import re
import uuid
from typing import AsyncGenerator, Literal

from app.repositories.chunk_repo import ChunkRepository
from app.repositories.cache_repo import CacheRepository
from app.schemas.study import StudyResponseDTO, SourceRef
from app.schemas.test import TestGenerateResponse, QuestionOut, QuestionOption
from app.schemas.quiz import QuizGenerateResponse, MCQOut, ShortAnswerOut
from app.services.rag_trace import RAGTrace, Timer
from app.services.circuit_breaker import llm_breaker, CircuitOpenError
from app.services.token_budget import trim_history
from app.middleware.safeguarding import tier1_scan
from rag.retrieval.pre_filter import build_filter
from rag.retrieval.rrf import reciprocal_rank_fusion
from rag.retrieval.prompt_builder import build_prompt, PROMPT_VERSION
from rag.retrieval.llm_client import generate_structured, embed_text, LLMResult

logger = logging.getLogger(__name__)

_STUDY_SCHEMA = {
    "type": "object",
    "properties": {
        "key_points": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"},
        "misconceptions": {"type": "array", "items": {"type": "string"}},
        "has_math": {"type": "boolean"},
    },
    "required": ["key_points", "notes", "misconceptions", "has_math"],
}

_TEST_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question_no": {"type": "integer"},
                    "question_text": {"type": "string"},
                    "option_a": {"type": "string"},
                    "option_b": {"type": "string"},
                    "option_c": {"type": "string"},
                    "option_d": {"type": "string"},
                    "correct_option": {"type": "string", "enum": ["A", "B", "C", "D"]},
                    "explanation": {"type": "string"},
                    "math_type": {"type": "string"},
                    "has_math": {"type": "boolean"},
                },
                # Without "required", schema-constrained generation (Ollama) may omit
                # option fields, and every question then fails the duplicate-option check.
                "required": [
                    "question_no", "question_text", "option_a", "option_b", "option_c",
                    "option_d", "correct_option", "explanation", "math_type", "has_math",
                ],
            },
        }
    },
    "required": ["questions"],
}

_QUIZ_SCHEMA = {
    "type": "object",
    "properties": {
        "mcqs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question_no": {"type": "integer"},
                    "question": {"type": "string"},
                    "option_a": {"type": "string"},
                    "option_b": {"type": "string"},
                    "option_c": {"type": "string"},
                    "option_d": {"type": "string"},
                    "answer": {"type": "string", "enum": ["A", "B", "C", "D"]},
                    "explanation": {"type": "string"},
                },
                "required": [
                    "question_no", "question", "option_a", "option_b", "option_c",
                    "option_d", "answer", "explanation",
                ],
            },
        },
        "short_answers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question_no": {"type": "integer"},
                    "question": {"type": "string"},
                    "answer": {"type": "string"},
                },
                "required": ["question_no", "question", "answer"],
            },
        },
    },
    "required": ["mcqs", "short_answers"],
}


class RetrievalService:
    def __init__(self, chunk_repo: ChunkRepository, db_session=None):
        self._chunks = chunk_repo
        self._db = db_session

    async def retrieve(
        self,
        query: str,
        class_num: int,
        subject: str,
        chapter: str | None,
        mode: Literal["study", "test", "quiz"],
        session_history: list[dict],
        medium: str = "en",
        trace_id: str | None = None,
    ) -> StudyResponseDTO | TestGenerateResponse | QuizGenerateResponse:
        trace = RAGTrace(
            query_text=query,
            mode=mode,
            subject=subject,
            class_num=class_num,
            chapter=chapter,
            medium=medium,
            prompt_version=PROMPT_VERSION,
            _db_session=self._db,
        )
        if trace_id:
            trace.trace_id = trace_id

        try:
            return await self._retrieve_inner(
                query, class_num, subject, chapter, mode, session_history, medium, trace
            )
        except CircuitOpenError:
            trace.error = "CircuitOpen"
            raise
        except Exception as exc:
            trace.error = type(exc).__name__
            raise
        finally:
            trace.emit()
            try:
                await trace.persist()
            except Exception:
                logger.warning("Failed to persist RAG trace %s", trace.trace_id)

    async def retrieve_streaming(
        self,
        query: str,
        class_num: int,
        subject: str,
        chapter: str | None,
        session_history: list[dict],
        medium: str = "en",
        trace_id: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """SSE generator for study mode — yields stage events + final result."""
        trace = RAGTrace(
            query_text=query,
            mode="study",
            subject=subject,
            class_num=class_num,
            chapter=chapter,
            medium=medium,
            prompt_version=PROMPT_VERSION,
            _db_session=self._db,
        )
        if trace_id:
            trace.trace_id = trace_id

        try:
            yield _sse("stage", {"stage": "embedding", "trace_id": trace.trace_id})

            trimmed_history = trim_history(session_history, medium)
            filters = build_filter(class_num, subject, chapter)
            content_lang = "hi" if subject == "hindi" else medium

            with Timer() as embed_t:
                embedding = await embed_text(query)
            trace.embed_ms = embed_t.ms

            # Check cache
            if self._db:
                try:
                    cache_repo = CacheRepository(self._db)
                    cached = await cache_repo.find_similar(
                        embedding, class_num, subject, chapter, medium
                    )
                    if cached and cached.prompt_version == PROMPT_VERSION:
                        trace.cache_hit = True
                        trace.total_ms = embed_t.ms
                        yield _sse("stage", {"stage": "cache_hit"})
                        if study_output_blocked(cached.response_json):
                            trace.error = "OutputBlocked"
                            yield _sse("error", {"message": OUTPUT_BLOCKED_MESSAGE})
                            return
                        yield _sse("result", cached.response_json)
                        yield _sse("done", {})
                        return
                except Exception:
                    await self._db.rollback()
                    logger.warning("Cache lookup failed for trace %s", trace.trace_id)

            yield _sse("stage", {"stage": "retrieving"})

            with Timer() as retrieval_t:
                vector_results = await self._chunks.vector_search(
                    embedding, filters["class_num"], filters["subject"], filters["chapter"],
                    language=content_lang,
                )
                bm25_results = await self._chunks.bm25_search(
                    query, filters["class_num"], filters["subject"], filters["chapter"],
                    language=content_lang,
                )
                trace.vector_hits = len(vector_results)
                trace.vector_top_score = round(vector_results[0][1], 4) if vector_results else 0.0
                trace.bm25_hits = len(bm25_results)
                trace.bm25_top_score = round(bm25_results[0][1], 4) if bm25_results else 0.0
                top_chunks = reciprocal_rank_fusion(vector_results, bm25_results, top_k=5)
                if not top_chunks:
                    trace.lang_fallback = True
                    vector_results = await self._chunks.vector_search(
                        embedding, filters["class_num"], filters["subject"], filters["chapter"],
                    )
                    bm25_results = await self._chunks.bm25_search(
                        query, filters["class_num"], filters["subject"], filters["chapter"],
                    )
                    top_chunks = reciprocal_rank_fusion(vector_results, bm25_results, top_k=5)

            trace.retrieval_ms = retrieval_t.ms
            trace.rrf_chunks = len(top_chunks)
            trace.chunk_chapters = list({c.chapter for c in top_chunks if c.chapter})
            trace.retrieved_chunks_preview = [
                c.text_content[:150] for c in top_chunks if c.text_content
            ]

            yield _sse("stage", {"stage": "retrieved", "chunks": trace.rrf_chunks})

            if not top_chunks:
                trace.empty_response = True
                empty = self._empty_response("study", content_lang)
                yield _sse("result", empty.model_dump())
                yield _sse("done", {})
                return

            yield _sse("stage", {"stage": "generating"})

            messages, system_instruction = build_prompt(
                "study", query, top_chunks, trimmed_history, medium=medium
            )
            async with llm_breaker:
                llm_result: LLMResult = await generate_structured(
                    messages, system_instruction, _STUDY_SCHEMA, max_output_tokens=2048
                )

            trace.llm_provider = llm_result.provider
            trace.llm_model = llm_result.model
            trace.llm_prompt_tokens = llm_result.prompt_tokens
            trace.llm_completion_tokens = llm_result.completion_tokens
            trace.llm_total_tokens = llm_result.total_tokens
            trace.llm_ms = llm_result.latency_ms
            trace.llm_json_valid = llm_result.json_valid
            try:
                trace.llm_response_preview = json.dumps(llm_result.data, ensure_ascii=False)[:500]
            except (TypeError, ValueError):
                trace.llm_response_preview = str(llm_result.data)[:500]

            parsed = self._parse_response("study", llm_result.data, top_chunks, trace)
            result_dict = parsed.model_dump()

            # Tier 1 safeguarding scan on OUTPUT before anything reaches the student or the cache
            if study_output_blocked(result_dict):
                trace.error = "OutputBlocked"
                yield _sse("error", {"message": OUTPUT_BLOCKED_MESSAGE})
                return

            # Store in cache
            if self._db and not trace.empty_response:
                try:
                    cache_repo = CacheRepository(self._db)
                    await cache_repo.store(
                        embedding=embedding,
                        class_num=class_num,
                        subject=subject,
                        chapter=chapter,
                        medium=medium,
                        query_text=query,
                        response_json=result_dict,
                        prompt_version=PROMPT_VERSION,
                    )
                except Exception:
                    logger.warning("Failed to cache response for trace %s", trace.trace_id)

            yield _sse("result", result_dict)
            yield _sse("done", {})

        except CircuitOpenError as e:
            trace.error = "CircuitOpen"
            yield _sse("error", {"message": str(e)})
        except Exception as exc:
            trace.error = type(exc).__name__
            yield _sse("error", {"message": "Rusty hit a snag. Please try again."})
        finally:
            trace.emit()
            try:
                await trace.persist()
            except Exception:
                logger.warning("Failed to persist RAG trace %s", trace.trace_id)

    async def _retrieve_inner(
        self,
        query: str,
        class_num: int,
        subject: str,
        chapter: str | None,
        mode: Literal["study", "test", "quiz"],
        session_history: list[dict],
        medium: str,
        trace: RAGTrace,
    ) -> StudyResponseDTO | TestGenerateResponse | QuizGenerateResponse:
        trimmed_history = trim_history(session_history, medium)
        filters = build_filter(class_num, subject, chapter)
        content_lang = "hi" if subject == "hindi" else medium

        # --- Embedding stage ---
        with Timer() as embed_t:
            embedding = await embed_text(query)
        trace.embed_ms = embed_t.ms

        # --- Cache check (study mode only) ---
        if mode == "study" and self._db:
            try:
                cache_repo = CacheRepository(self._db)
                cached = await cache_repo.find_similar(
                    embedding, class_num, subject, chapter, medium
                )
                if cached and cached.prompt_version == PROMPT_VERSION:
                    trace.cache_hit = True
                    return StudyResponseDTO(**cached.response_json)
            except Exception:
                await self._db.rollback()
                logger.warning("Cache lookup failed for trace %s", trace.trace_id)

        # --- Retrieval stage ---
        with Timer() as retrieval_t:
            vector_results = await self._chunks.vector_search(
                embedding, filters["class_num"], filters["subject"], filters["chapter"],
                language=content_lang,
            )
            bm25_results = await self._chunks.bm25_search(
                query, filters["class_num"], filters["subject"], filters["chapter"],
                language=content_lang,
            )

            trace.vector_hits = len(vector_results)
            trace.vector_top_score = round(vector_results[0][1], 4) if vector_results else 0.0
            trace.bm25_hits = len(bm25_results)
            trace.bm25_top_score = round(bm25_results[0][1], 4) if bm25_results else 0.0

            top_chunks = reciprocal_rank_fusion(vector_results, bm25_results, top_k=5)

            if not top_chunks:
                trace.lang_fallback = True
                vector_results = await self._chunks.vector_search(
                    embedding, filters["class_num"], filters["subject"], filters["chapter"],
                )
                bm25_results = await self._chunks.bm25_search(
                    query, filters["class_num"], filters["subject"], filters["chapter"],
                )
                top_chunks = reciprocal_rank_fusion(vector_results, bm25_results, top_k=5)

        trace.retrieval_ms = retrieval_t.ms
        trace.rrf_chunks = len(top_chunks)
        trace.chunk_chapters = list({c.chapter for c in top_chunks if c.chapter})
        trace.retrieved_chunks_preview = [
            c.text_content[:150] for c in top_chunks if c.text_content
        ]

        if not top_chunks:
            trace.empty_response = True
            return self._empty_response(mode, content_lang)

        if mode == "test":
            top_chunks = list(top_chunks)
            random.shuffle(top_chunks)

        messages, system_instruction = build_prompt(mode, query, top_chunks, trimmed_history, medium=medium)

        schema = {"study": _STUDY_SCHEMA, "test": _TEST_SCHEMA, "quiz": _QUIZ_SCHEMA}[mode]
        max_tokens = 4096 if mode in ("test", "quiz") else 2048

        # --- LLM stage (circuit-breaker protected) ---
        async with llm_breaker:
            llm_result: LLMResult = await generate_structured(
                messages, system_instruction, schema, max_output_tokens=max_tokens
            )

        trace.llm_provider = llm_result.provider
        trace.llm_model = llm_result.model
        trace.llm_prompt_tokens = llm_result.prompt_tokens
        trace.llm_completion_tokens = llm_result.completion_tokens
        trace.llm_total_tokens = llm_result.total_tokens
        trace.llm_ms = llm_result.latency_ms
        trace.llm_json_valid = llm_result.json_valid
        try:
            trace.llm_response_preview = json.dumps(llm_result.data, ensure_ascii=False)[:500]
        except (TypeError, ValueError):
            trace.llm_response_preview = str(llm_result.data)[:500]

        result = self._parse_response(mode, llm_result.data, top_chunks, trace)

        # --- Cache store (study mode, successful responses only) ---
        if mode == "study" and self._db and not trace.empty_response and isinstance(result, StudyResponseDTO):
            try:
                cache_repo = CacheRepository(self._db)
                await cache_repo.store(
                    embedding=embedding,
                    class_num=class_num,
                    subject=subject,
                    chapter=chapter,
                    medium=medium,
                    query_text=query,
                    response_json=result.model_dump(),
                    prompt_version=PROMPT_VERSION,
                )
            except Exception:
                logger.warning("Failed to cache response for trace %s", trace.trace_id)

        return result

    def _parse_response(self, mode: str, raw: dict, chunks: list, trace: RAGTrace):
        if mode == "study":
            sources = _extract_sources(chunks)
            key_points = raw.get("key_points", [])
            misconceptions = raw.get("misconceptions", [])
            notes = raw.get("notes", "")

            chunk_text = " ".join(c.text_content for c in chunks).lower()
            kept_kp = _ground_check(key_points, chunk_text)
            kept_misc = _ground_check(misconceptions, chunk_text)

            trace.ground_check_kept = len(kept_kp) + len(kept_misc)
            trace.ground_check_dropped = (len(key_points) + len(misconceptions)) - trace.ground_check_kept
            trace.empty_response = not notes and not kept_kp
            if trace.empty_response:
                # Model found nothing relevant in the passages — tell the student
                # instead of rendering a blank answer.
                notes = self._empty_response("study", trace.medium).notes
                kept_misc = []

            return StudyResponseDTO(
                key_points=kept_kp,
                notes=notes,
                misconceptions=kept_misc,
                source_chunks=len(chunks),
                has_math=raw.get("has_math", False),
                sources=sources,
            )
        if mode == "test":
            questions = []
            valid_raw = []
            total_generated = len(raw.get("questions", []))
            for q in raw.get("questions", []):
                if not _validate_mcq(q):
                    continue
                q["correct_option"] = q["correct_option"].strip().upper()
                questions.append(
                    QuestionOut(
                        question_id=uuid.uuid4(),
                        question_no=len(questions) + 1,
                        question_text=q["question_text"],
                        options=QuestionOption(
                            A=q["option_a"], B=q["option_b"],
                            C=q["option_c"], D=q["option_d"],
                        ),
                        has_math=q.get("has_math", False),
                    )
                )
                q["question_no"] = len(questions)
                valid_raw.append(q)

            trace.mcq_generated = total_generated
            trace.mcq_dropped = total_generated - len(questions)
            trace.empty_response = len(questions) == 0
            return questions, valid_raw
        if mode == "quiz":
            mcqs = []
            total_mcqs = len(raw.get("mcqs", []))
            for m in raw.get("mcqs", []):
                if not _validate_mcq(m, ("option_a", "option_b", "option_c", "option_d")):
                    continue
                m["answer"] = m["answer"].strip().upper()
                mcqs.append(MCQOut(
                    question_no=len(mcqs) + 1,
                    question=m["question"],
                    options={"A": m["option_a"], "B": m["option_b"],
                             "C": m["option_c"], "D": m["option_d"]},
                    answer=m["answer"],
                    explanation=m["explanation"],
                ))
            short = [
                ShortAnswerOut(
                    question_no=s["question_no"],
                    question=s["question"],
                    answer=s["answer"],
                )
                for s in raw.get("short_answers", [])
            ]
            trace.mcq_generated = total_mcqs
            trace.mcq_dropped = total_mcqs - len(mcqs)
            trace.empty_response = len(mcqs) == 0 and len(short) == 0
            plain = _build_plain_text(mcqs, short)
            return QuizGenerateResponse(mcqs=mcqs, short_answers=short, plain_text=plain)

    def _empty_response(self, mode: str, language: str = "en"):
        not_found_en = "I couldn't find that in your textbook. Ask your teacher for help."
        not_found_hi = "यह मुझे तुम्हारी पाठ्यपुस्तक में नहीं मिला। अपने शिक्षक से पूछो।"
        not_found = not_found_hi if language == "hi" else not_found_en

        if mode == "study":
            return StudyResponseDTO(
                key_points=[],
                notes=not_found,
                misconceptions=[],
                source_chunks=0,
                has_math=False,
            )
        if mode == "test":
            return [], []
        if mode == "quiz":
            return QuizGenerateResponse(mcqs=[], short_answers=[], plain_text=not_found)


_NUM_RE = re.compile(r"\d+")
_NEGATIVE_RE = re.compile(
    r"\b(not|n['']t|except|neither|never|none of|which.{0,15}(?:isn['']t|aren['']t|is not|are not))\b",
    re.IGNORECASE,
)


def _validate_mcq(q: dict, option_keys: tuple[str, ...] = ("option_a", "option_b", "option_c", "option_d")) -> bool:
    correct = q.get("correct_option") or q.get("answer", "")
    correct = correct.strip().upper()
    if correct not in ("A", "B", "C", "D"):
        logger.warning("MCQ dropped: invalid correct_option %r", correct)
        return False

    options = [str(q.get(k, "")).strip().lower() for k in option_keys]
    if len(set(options)) < len(options):
        logger.warning("MCQ dropped: duplicate options")
        return False

    text = q.get("question_text") or q.get("question", "")
    if _NEGATIVE_RE.search(text):
        logger.warning("MCQ dropped: negative phrasing in %r", text[:80])
        return False

    return True


def _ground_check(claims: list[str], chunk_text: str, min_overlap: int = 2) -> list[str]:
    grounded = []
    for claim in claims:
        claim_words = set(claim.lower().split())
        stop = {"the", "a", "an", "is", "are", "was", "were", "of", "in", "to",
                "and", "or", "for", "it", "that", "this", "with", "from", "by",
                "on", "at", "as", "be", "has", "had", "have", "not", "no", "can"}
        content_words = claim_words - stop
        if not content_words:
            grounded.append(claim)
            continue
        matches = sum(1 for w in content_words if w in chunk_text)
        if matches >= min(min_overlap, len(content_words)):
            grounded.append(claim)
    return grounded


def _extract_sources(chunks: list) -> list[SourceRef]:
    seen = set()
    sources = []
    for c in chunks:
        key = (c.chapter, getattr(c, "page_num", None), c.source_pdf)
        if key not in seen:
            seen.add(key)
            sources.append(SourceRef(
                chapter=c.chapter or None,
                page_num=getattr(c, "page_num", None),
                source_pdf=c.source_pdf,
            ))
    return sources


def _build_plain_text(mcqs: list[MCQOut], short: list[ShortAnswerOut]) -> str:
    lines = ["=== MCQ Questions ===\n"]
    for m in mcqs:
        lines.append(f"Q{m.question_no}. {m.question}")
        for letter, text in m.options.items():
            lines.append(f"   {letter}. {text}")
        lines.append(f"   Answer: {m.answer}\n")
    lines.append("\n=== Short Answer Questions ===\n")
    for s in short:
        lines.append(f"Q{s.question_no}. {s.question}")
        lines.append(f"   Answer: {s.answer}\n")
    return "\n".join(lines)


OUTPUT_BLOCKED_MESSAGE = "Response could not be delivered. Please try a different question."


def study_output_blocked(result: dict) -> bool:
    """Tier 1 scan over every model-written field a study answer shows the student."""
    text = " ".join([
        *(result.get("key_points") or []),
        result.get("notes") or "",
        *(result.get("misconceptions") or []),
    ])
    return tier1_scan(text)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
