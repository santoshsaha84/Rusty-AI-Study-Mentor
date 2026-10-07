import hashlib
import json as _json
import uuid
import tempfile
from pathlib import Path

import structlog
from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form, status, BackgroundTasks
from sqlalchemy import select, delete, func

from app.core.deps import AdminDep, TeacherDep, DBDep
from app.models.textbook import Textbook
from app.models.chunk import Chunk
from app.models.chapter_summary import ChapterSummary
from app.repositories.trace_repo import TraceRepository
from app.services.ingestion import run_ingestion

log = structlog.get_logger(__name__)


def _hash_id(raw_id: str) -> str:
    return hashlib.sha256(raw_id.encode()).hexdigest()[:16]

router = APIRouter()

VALID_SUBJECTS = {"mathematics", "science", "hindi", "social_science", "english"}
MAX_PDF_SIZE = 50 * 1024 * 1024  # 50 MB


async def _start_ingest_job_or_fail(db, textbook: Textbook) -> None:
    """Start the rusty-ingest Cloud Run Job; mark the textbook failed if it can't be started."""
    from app.services import gcp
    try:
        await gcp.start_ingest_job()
    except Exception as exc:
        log.error("ingest_job_start_failed", source_pdf=textbook.source_pdf, error=type(exc).__name__)
        textbook.status = "failed"
        textbook.error_message = "Could not start the ingestion job. Try again or ask the tech lead."
        await db.commit()
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=textbook.error_message)


@router.post("/upload-pdf")
async def upload_pdf(
    background_tasks: BackgroundTasks,
    user: AdminDep,
    db: DBDep,
    file: UploadFile = File(...),
    class_num: int = Form(...),
    subject: str = Form(...),
    chapter: str = Form(default=""),
):
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only PDF files are allowed.")

    if subject.lower().strip() not in VALID_SUBJECTS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid subject.")

    if class_num < 5 or class_num > 10:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Class must be 5–10.")

    content = await file.read()
    if len(content) > MAX_PDF_SIZE:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="PDF must be under 50 MB.")

    if not content[:5].startswith(b"%PDF-"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File content is not a valid PDF.")

    subject_clean = subject.lower().strip()
    chapter_clean = chapter.strip() or None
    source_pdf = f"class{class_num}_{subject_clean}_{file.filename}"

    existing = await db.execute(
        select(Textbook).where(Textbook.source_pdf == source_pdf)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"This file already exists. Delete it first to re-upload.",
        )

    from app.core.config import get_settings
    settings = get_settings()
    textbook_id = uuid.uuid4()

    if settings.uses_cloud_ingestion:
        # Cloud Run throttles CPU once the response is sent, so a BackgroundTask would
        # stall — store the PDF in Cloud Storage and hand off to the rusty-ingest job.
        from app.services import gcp
        await gcp.upload_textbook_pdf(str(textbook_id), content)

    textbook = Textbook(
        textbook_id=textbook_id,
        source_pdf=source_pdf,
        original_filename=file.filename,
        class_num=class_num,
        subject=subject_clean,
        chapter=chapter_clean,
        status="processing",
        uploaded_by=user.student_id,
    )
    db.add(textbook)
    await db.flush()

    if settings.uses_cloud_ingestion:
        # The job finds work by reading textbooks rows, so the row must be committed first.
        await db.commit()
        await _start_ingest_job_or_fail(db, textbook)
    else:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        tmp.write(content)
        tmp.close()
        background_tasks.add_task(
            run_ingestion,
            pdf_path=tmp.name,
            source_pdf=source_pdf,
            class_num=class_num,
            subject=subject_clean,
            chapter=chapter_clean,
            database_url=settings.database_url,
        )

    log.info("ingestion_started", source_pdf=source_pdf, uploaded_by_hash=_hash_id(user.student_id))

    return {
        "textbook_id": str(textbook.textbook_id),
        "source_pdf": source_pdf,
        "status": "processing",
        "message": "PDF uploaded. Ingestion started in the background.",
    }


@router.get("/textbooks")
async def list_textbooks(user: AdminDep, db: DBDep):
    result = await db.execute(
        select(Textbook).order_by(Textbook.uploaded_at.desc())
    )
    textbooks = result.scalars().all()
    return {
        "textbooks": [
            {
                "textbook_id": str(t.textbook_id),
                "source_pdf": t.source_pdf,
                "original_filename": t.original_filename,
                "class_num": t.class_num,
                "subject": t.subject,
                "chapter": t.chapter,
                "language": t.language,
                "chunk_count": t.chunk_count,
                "status": t.status,
                "error_message": t.error_message,
                "uploaded_at": t.uploaded_at.isoformat() if t.uploaded_at else None,
            }
            for t in textbooks
        ]
    }


@router.delete("/textbooks/{textbook_id}")
async def delete_textbook(textbook_id: str, user: AdminDep, db: DBDep):
    result = await db.execute(
        select(Textbook).where(Textbook.textbook_id == textbook_id)
    )
    textbook = result.scalar_one_or_none()
    if not textbook:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Textbook not found.")

    if textbook.status == "processing":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete while ingestion is in progress. Wait for it to finish.",
        )

    await db.execute(
        delete(ChapterSummary).where(ChapterSummary.source_pdf == textbook.source_pdf)
    )

    await db.execute(
        delete(Chunk).where(Chunk.source_pdf == textbook.source_pdf)
    )

    await db.execute(
        delete(Textbook).where(Textbook.textbook_id == textbook_id)
    )

    log.info("textbook_deleted", source_pdf=textbook.source_pdf, deleted_by_hash=_hash_id(user.student_id))

    return {"success": True, "deleted_chunks": textbook.chunk_count}


# ──────────────── RAG Observability Dashboard ────────────────


@router.get("/rag/summary")
async def rag_summary(
    user: TeacherDep,
    db: DBDep,
    days: int = Query(default=7, ge=1, le=180),
):
    repo = TraceRepository(db)
    return await repo.summary_stats(days)


@router.get("/rag/by-subject")
async def rag_by_subject(
    user: TeacherDep,
    db: DBDep,
    days: int = Query(default=7, ge=1, le=180),
):
    repo = TraceRepository(db)
    return {"subjects": await repo.by_subject(days)}


@router.get("/rag/timeline")
async def rag_timeline(
    user: TeacherDep,
    db: DBDep,
    days: int = Query(default=7, ge=1, le=180),
):
    repo = TraceRepository(db)
    return {"timeline": await repo.latency_timeline(days)}


@router.get("/rag/recent")
async def rag_recent(
    user: TeacherDep,
    db: DBDep,
    limit: int = Query(default=50, ge=1, le=200),
):
    repo = TraceRepository(db)
    rows = await repo.recent(limit)
    return {
        "traces": [
            {
                "trace_id": r.trace_id,
                "created_at": r.created_at.isoformat(),
                "query_text": r.query_text or "",
                "mode": r.mode,
                "subject": r.subject,
                "class_num": r.class_num,
                "chapter": r.chapter or "",
                "medium": r.medium,
                "total_ms": r.total_ms,
                "embed_ms": r.embed_ms,
                "retrieval_ms": r.retrieval_ms,
                "llm_ms": r.llm_ms,
                "llm_model": r.llm_model,
                "llm_total_tokens": r.llm_total_tokens,
                "vector_hits": r.vector_hits,
                "bm25_hits": r.bm25_hits,
                "rrf_chunks": r.rrf_chunks,
                "empty_response": r.empty_response,
                "error": r.error,
                "vector_top_score": r.vector_top_score,
                "llm_json_valid": r.llm_json_valid,
                "ground_check_kept": r.ground_check_kept,
                "ground_check_dropped": r.ground_check_dropped,
                "mcq_generated": r.mcq_generated,
                "mcq_dropped": r.mcq_dropped,
                "retrieved_chunks_preview": r.retrieved_chunks_preview or [],
                "llm_response_preview": r.llm_response_preview or "",
            }
            for r in rows
        ]
    }


@router.post("/rag/purge")
async def rag_purge(user: AdminDep, db: DBDep):
    repo = TraceRepository(db)
    purged = await repo.purge_expired()
    return {"purged": purged}


@router.post("/textbooks/{textbook_id}/regenerate-summaries")
async def regenerate_summaries(
    textbook_id: str,
    background_tasks: BackgroundTasks,
    user: AdminDep,
    db: DBDep,
):
    result = await db.execute(
        select(Textbook).where(Textbook.textbook_id == textbook_id)
    )
    textbook = result.scalar_one_or_none()
    if not textbook:
        raise HTTPException(status_code=404, detail="Textbook not found.")
    if textbook.status != "ready":
        raise HTTPException(status_code=409, detail="Textbook must be in 'ready' state.")

    from app.core.config import get_settings
    settings = get_settings()

    if settings.uses_cloud_ingestion:
        # No long-running background work on Cloud Run: the ingest job re-processes the PDF
        # from Cloud Storage (idempotent — chunks are replaced) and regenerates summaries.
        textbook.status = "processing"
        await db.commit()
        await _start_ingest_job_or_fail(db, textbook)
        return {"status": "regeneration_started", "source_pdf": textbook.source_pdf}

    async def _regen(source_pdf: str, class_num: int, subject: str, lang: str) -> None:
        try:
            from rag.ingestion.summary_generator import generate_chapter_summaries
            count = await generate_chapter_summaries(
                database_url=settings.database_url,
                source_pdf=source_pdf,
                class_num=class_num,
                subject=subject,
                detected_lang=lang,
            )
            log.info("summaries_regenerated", source_pdf=source_pdf, count=count)
        except Exception as exc:
            log.error("summary_regen_failed", source_pdf=source_pdf, error=str(exc))

    background_tasks.add_task(
        _regen, textbook.source_pdf, textbook.class_num, textbook.subject, textbook.language or "en"
    )

    return {"status": "regeneration_started", "source_pdf": textbook.source_pdf}


# ──────────────── Eval Results ────────────────

_EVAL_RESULTS_DIR = Path(__file__).resolve().parent.parent.parent / "eval" / "results"


@router.get("/eval/runs")
async def eval_runs(user: TeacherDep):
    if not _EVAL_RESULTS_DIR.is_dir():
        return {"runs": []}

    runs = []
    for f in sorted(_EVAL_RESULTS_DIR.glob("*.json"), reverse=True):
        try:
            data = _json.loads(f.read_text())
            runs.append({
                "filename": f.name,
                "dataset": data.get("dataset", f.stem),
                "mode": data.get("mode", "study"),
                "timestamp": data.get("timestamp", ""),
                "total": data.get("total", 0),
                "passed": data.get("passed", 0),
            })
        except Exception:
            continue
    return {"runs": runs}


@router.get("/eval/run/{filename}")
async def eval_run_detail(filename: str, user: TeacherDep):
    safe = Path(filename).name
    filepath = _EVAL_RESULTS_DIR / safe
    if not filepath.is_file() or not safe.endswith(".json"):
        raise HTTPException(status_code=404, detail="Eval run not found.")
    return _json.loads(filepath.read_text())
