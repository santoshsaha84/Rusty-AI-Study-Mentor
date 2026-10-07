"""
Cloud Run Job entry point for textbook ingestion (`rusty-ingest`).
Usage (container):  python -m rag.ingestion.job

The API uploads the PDF to gs://$TEXTBOOK_BUCKET/textbooks/<textbook_id>.pdf, leaves the
`textbooks` row at status='processing' and starts an execution without overrides. Each
execution processes every row still at 'processing', so it is safe to retry or run twice:
a Postgres advisory lock stops two executions working on the same textbook, and
ingest_pdf replaces any chunks a previous attempt left behind.
"""
import asyncio
import os
import sys
import tempfile

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.services import gcp
from app.services.ingestion import run_ingestion, update_textbook

log = structlog.get_logger("rag.ingestion.job")


async def _pending_textbooks(engine) -> list:
    async with engine.connect() as conn:
        rows = await conn.execute(text(
            "SELECT textbook_id, source_pdf, class_num, subject, chapter FROM textbooks "
            "WHERE status = 'processing' ORDER BY uploaded_at"
        ))
        return rows.fetchall()


async def _process(engine, row, database_url: str) -> None:
    textbook_id = str(row.textbook_id)
    # Session-level advisory lock, held on its own connection for the whole ingestion.
    async with engine.connect() as lock_conn:
        got_lock = (await lock_conn.execute(
            text("SELECT pg_try_advisory_lock(hashtextextended(:k, 0))"), {"k": textbook_id}
        )).scalar()
        if not got_lock:
            log.info("ingest_skip_locked", source_pdf=row.source_pdf)
            return
        try:
            fd, pdf_path = tempfile.mkstemp(suffix=".pdf")
            os.close(fd)
            try:
                await gcp.download_textbook_pdf(textbook_id, pdf_path)
            except Exception as exc:
                os.unlink(pdf_path)
                log.error("ingest_download_failed", source_pdf=row.source_pdf, error=type(exc).__name__)
                await update_textbook(
                    database_url, row.source_pdf, status="failed",
                    error_message="PDF not found in Cloud Storage. Delete and upload it again.",
                )
                return
            # run_ingestion records success/failure on the row and deletes the temp file.
            await run_ingestion(
                pdf_path=pdf_path,
                source_pdf=row.source_pdf,
                class_num=row.class_num,
                subject=row.subject,
                chapter=row.chapter,
                database_url=database_url,
            )
        finally:
            await lock_conn.execute(
                text("SELECT pg_advisory_unlock(hashtextextended(:k, 0))"), {"k": textbook_id}
            )


async def main() -> int:
    settings = get_settings()
    if not settings.uses_cloud_ingestion:
        log.error("ingest_job_misconfigured", reason="TEXTBOOK_BUCKET is not set")
        return 2

    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        pending = await _pending_textbooks(engine)
        log.info("ingest_job_start", pending=len(pending))
        for row in pending:
            await _process(engine, row, settings.database_url)
    finally:
        await engine.dispose()
    log.info("ingest_job_done")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
