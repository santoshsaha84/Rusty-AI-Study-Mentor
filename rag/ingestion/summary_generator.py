"""
Post-ingestion chapter summary generation.
Fetches all chunks for a chapter, sends to LLM, stores structured summary.
Generates both EN and HI versions when the source language is English.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, text as sql_text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

import structlog

from rag.retrieval.llm_client import generate_structured

log = structlog.get_logger("rag.summary")

_SUMMARY_PROMPT_EN = (
    "Summarize this textbook chapter for students. Return a JSON object with these exact keys:\n"
    "{\"summary\": \"...\", \"key_topics\": [\"...\"], \"important_formulas\": [\"...\"], \"important_definitions\": [\"...\"]}\n\n"
    "Rules: base summary ONLY on provided passages. Keep language simple. "
    "Include all formulas and definitions found. "
    "summary: 150-300 words. key_topics: 3-8 items. "
    "important_formulas and important_definitions: empty list [] if none found."
)

_SUMMARY_PROMPT_HI = (
    "इस पाठ्यपुस्तक अध्याय का हिंदी में सारांश बनाओ। JSON object इन keys के साथ:\n"
    "{\"summary\": \"...\", \"key_topics\": [\"...\"], \"important_formulas\": [\"...\"], \"important_definitions\": [\"...\"]}\n\n"
    "नियम: सारांश केवल दिए गए अंशों पर आधारित हो। भाषा सरल रखो। "
    "सभी सूत्र और परिभाषाएँ शामिल करो। "
    "summary: 150-300 शब्द। key_topics: 3-8 आइटम। "
    "सब values हिंदी में। JSON keys अंग्रेज़ी में। "
    "important_formulas और important_definitions: नहीं हो तो खाली सूची []।"
)

MAX_CHUNKS_FOR_SUMMARY = 15
MAX_CHARS_PER_CHUNK = 400

_SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "key_topics": {"type": "array", "items": {"type": "string"}},
        "important_formulas": {"type": "array", "items": {"type": "string"}},
        "important_definitions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "key_topics", "important_formulas", "important_definitions"],
}


async def generate_chapter_summaries(
    database_url: str,
    source_pdf: str,
    class_num: int,
    subject: str,
    detected_lang: str,
) -> int:
    engine = create_async_engine(database_url, echo=False)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    generated = 0
    try:
        async with factory() as session:
            # Get distinct chapters for this PDF
            rows = (await session.execute(
                sql_text(
                    "SELECT DISTINCT chapter FROM chunks "
                    "WHERE source_pdf = :source_pdf AND chapter IS NOT NULL AND chapter != ''"
                ),
                {"source_pdf": source_pdf},
            )).fetchall()

            chapters = [r.chapter for r in rows]
            if not chapters:
                log.info("summary_skip_no_chapters", source_pdf=source_pdf)
                return 0

            for chapter in chapters:
                # Fetch all chunks for this chapter
                chunk_rows = (await session.execute(
                    sql_text(
                        "SELECT text_content FROM chunks "
                        "WHERE source_pdf = :source_pdf AND chapter = :chapter "
                        "ORDER BY page_num NULLS LAST, chunk_id"
                    ),
                    {"source_pdf": source_pdf, "chapter": chapter},
                )).fetchall()

                if not chunk_rows:
                    continue

                texts = [r.text_content for r in chunk_rows]
                chunk_count = len(texts)

                # Sample evenly if too many chunks
                if len(texts) > MAX_CHUNKS_FOR_SUMMARY:
                    step = len(texts) / MAX_CHUNKS_FOR_SUMMARY
                    texts = [texts[int(i * step)] for i in range(MAX_CHUNKS_FOR_SUMMARY)]

                # Truncate individual chunks to fit context
                texts = [t[:MAX_CHARS_PER_CHUNK] for t in texts]
                passage_text = "\n\n---\n\n".join(
                    f"[Passage {i+1}]\n{t}" for i, t in enumerate(texts)
                )

                # Generate EN summary
                en_summary = await _generate_one(
                    passage_text, _SUMMARY_PROMPT_EN
                )
                if en_summary:
                    await _upsert_summary(
                        session, class_num, subject, chapter, "en",
                        en_summary, chunk_count, source_pdf,
                    )
                    generated += 1

                # Generate HI summary (for all subjects — Hindi-medium students need it)
                hi_summary = await _generate_one(
                    passage_text, _SUMMARY_PROMPT_HI
                )
                if hi_summary:
                    await _upsert_summary(
                        session, class_num, subject, chapter, "hi",
                        hi_summary, chunk_count, source_pdf,
                    )
                    generated += 1

            await session.commit()
    finally:
        await engine.dispose()

    log.info("summary_generation_complete", source_pdf=source_pdf, summaries_generated=generated)
    return generated


async def _generate_one(
    passage_text: str,
    system_prompt: str,
) -> dict | None:
    messages = [
        {"role": "user", "parts": [{"text": f"Textbook passages:\n\n{passage_text}"}]},
    ]

    for attempt in range(2):
        try:
            result = await generate_structured(
                messages, system_prompt, _SUMMARY_SCHEMA, max_output_tokens=2048
            )
            data = _normalize_keys(result.data)
            if not data.get("summary"):
                log.warning("summary_incomplete", keys=list(data.keys()), attempt=attempt)
                continue
            if not data.get("key_topics"):
                data["key_topics"] = []

            return data
        except Exception as exc:
            log.error("summary_generation_failed", error=str(exc), attempt=attempt)

    return None


def _normalize_keys(data: dict) -> dict:
    key_map = {
        "response": "summary", "overview": "summary", "chapter_summary": "summary",
        "answer": "summary", "text": "summary",
        "topics": "key_topics", "main_topics": "key_topics",
        "formulas": "important_formulas",
        "definitions": "important_definitions",
    }
    normalized = {}
    for k, v in data.items():
        normalized[key_map.get(k, k)] = v
    if "summary" in normalized and "key_topics" not in normalized:
        summary_text = normalized["summary"]
        if isinstance(summary_text, str) and len(summary_text) > 50:
            normalized["key_topics"] = []
    return normalized


async def _upsert_summary(
    session: AsyncSession,
    class_num: int,
    subject: str,
    chapter: str,
    language: str,
    data: dict,
    chunk_count: int,
    source_pdf: str,
) -> None:
    # Delete existing summary for this combo (upsert)
    await session.execute(
        sql_text(
            "DELETE FROM chapter_summaries "
            "WHERE class_num = :class_num AND subject = :subject "
            "AND chapter = :chapter AND language = :language"
        ),
        {"class_num": class_num, "subject": subject, "chapter": chapter, "language": language},
    )

    await session.execute(
        sql_text(
            "INSERT INTO chapter_summaries "
            "(id, class_num, subject, chapter, language, summary, key_topics, "
            "important_formulas, important_definitions, chunk_count, source_pdf) "
            "VALUES (:id, :class_num, :subject, :chapter, :language, :summary, "
            ":key_topics, :formulas, :definitions, :chunk_count, :source_pdf)"
        ),
        {
            "id": str(uuid.uuid4()),
            "class_num": class_num,
            "subject": subject,
            "chapter": chapter,
            "language": language,
            "summary": data["summary"],
            "key_topics": data.get("key_topics", []),
            "formulas": data.get("important_formulas") or [],
            "definitions": data.get("important_definitions") or [],
            "chunk_count": chunk_count,
            "source_pdf": source_pdf,
        },
    )
