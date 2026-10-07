"""
Textbook ingestion runner shared by the API (local: FastAPI BackgroundTask) and the
`rusty-ingest` Cloud Run Job (GCP: rag.ingestion.job). Updates `textbooks.status` as it goes.
"""
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

_TEXTBOOK_UPDATE_FIELDS = {"status", "chunk_count", "language", "error_message"}


async def update_textbook(database_url: str, source_pdf: str, **fields) -> None:
    from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
    from sqlalchemy import text as sql_text
    invalid = set(fields.keys()) - _TEXTBOOK_UPDATE_FIELDS
    if invalid:
        raise ValueError(f"Disallowed fields in textbook update: {invalid}")
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        await session.execute(
            sql_text(f"UPDATE textbooks SET {sets} WHERE source_pdf = :source_pdf"),
            {"source_pdf": source_pdf, **fields},
        )
        await session.commit()
    await engine.dispose()


async def run_ingestion(
    pdf_path: str,
    source_pdf: str,
    class_num: int,
    subject: str,
    chapter: str | None,
    database_url: str,
) -> None:
    from rag.ingestion.pipeline import ingest_pdf

    try:
        from rag.ingestion.language_detect import detect_language
        from rag.ingestion.pdf_extractor import extract_pages

        pages = extract_pages(pdf_path)
        sample_text = " ".join(p["text"][:200] for p in pages[:5])
        detected_lang = detect_language(sample_text)

        count = await ingest_pdf(
            pdf_path=pdf_path,
            class_num=class_num,
            subject=subject,
            database_url=database_url,
            source_pdf_name=source_pdf,
        )

        if chapter:
            from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
            from sqlalchemy import text as sql_text
            engine = create_async_engine(database_url)
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                await session.execute(
                    sql_text("UPDATE chunks SET chapter = :chapter WHERE source_pdf = :source_pdf AND (chapter IS NULL OR chapter = '')"),
                    {"chapter": chapter, "source_pdf": source_pdf},
                )
                await session.commit()
            await engine.dispose()

        await update_textbook(database_url, source_pdf, status="ready", chunk_count=count, language=detected_lang)
        log.info("ingestion_complete", source_pdf=source_pdf, chunk_count=count)

        # Generate chapter summaries (EN + HI) after chunks are ready
        try:
            from rag.ingestion.summary_generator import generate_chapter_summaries
            summary_count = await generate_chapter_summaries(
                database_url=database_url,
                source_pdf=source_pdf,
                class_num=class_num,
                subject=subject,
                detected_lang=detected_lang,
            )
            log.info("summaries_generated", source_pdf=source_pdf, count=summary_count)
        except Exception as summary_exc:
            log.error("summary_generation_failed", source_pdf=source_pdf, error=str(summary_exc))

    except Exception as exc:
        log.error("ingestion_failed", source_pdf=source_pdf, error=str(exc))
        await update_textbook(database_url, source_pdf, status="failed", error_message=str(exc)[:500])

    finally:
        pdf = Path(pdf_path)
        if pdf.exists():
            pdf.unlink()
