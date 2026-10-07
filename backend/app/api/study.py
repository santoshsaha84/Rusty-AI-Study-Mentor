from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from app.core.deps import StudentDep, DBDep
from app.core.limiter import limiter
from app.middleware.safeguarding import tier1_scan, SAFE_REDIRECT_MESSAGE
from app.schemas.study import StudyQueryRequest, StudyResponseDTO
from app.repositories.chunk_repo import ChunkRepository
from app.repositories.summary_repo import SummaryRepository
from app.services.retrieval import RetrievalService, study_output_blocked, OUTPUT_BLOCKED_MESSAGE
from app.services.circuit_breaker import CircuitOpenError

router = APIRouter()

VALID_SUBJECTS = {"mathematics", "science", "hindi", "social_science", "english"}


@router.get("/chapters")
async def list_chapters(
    user: StudentDep,
    db: DBDep,
    class_num: int | None = Query(default=None),
    subject: str | None = Query(default=None, max_length=50),
):
    """Return available subjects and chapters from uploaded textbooks."""
    cn = class_num or user.class_num or 5
    repo = ChunkRepository(db)

    if subject:
        subj = subject.lower().strip()
        if subj not in VALID_SUBJECTS:
            raise HTTPException(status_code=422, detail="Invalid subject")
        chapters = await repo.distinct_chapters(cn, subj)
        return {"class_num": cn, "subject": subj, "chapters": chapters}

    available_subjects = await repo.available_subjects(cn)
    result: dict[str, list[str]] = {}
    for subj in available_subjects:
        result[subj] = await repo.distinct_chapters(cn, subj)
    return {"class_num": cn, "subjects": result}


@router.get("/summary")
async def chapter_summary(
    user: StudentDep,
    db: DBDep,
    subject: str = Query(max_length=50),
    chapter: str = Query(max_length=100),
    medium: str = Query(default="en", max_length=2),
    class_num: int | None = Query(default=None),
):
    subj = subject.lower().strip()
    if subj not in VALID_SUBJECTS:
        raise HTTPException(status_code=422, detail="Invalid subject")

    cn = class_num or user.class_num or 5
    repo = SummaryRepository(db)
    summary = await repo.get(cn, subj, chapter, medium)

    if not summary:
        # Try English fallback for Hindi-medium students
        if medium == "hi":
            summary = await repo.get(cn, subj, chapter, "en")
        if not summary:
            raise HTTPException(
                status_code=404,
                detail="Summary not available for this chapter yet.",
            )

    if study_output_blocked(_summary_to_study_response(summary).model_dump()):
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=OUTPUT_BLOCKED_MESSAGE)

    return {
        "class_num": summary.class_num,
        "subject": summary.subject,
        "chapter": summary.chapter,
        "language": summary.language,
        "summary": summary.summary,
        "key_topics": summary.key_topics,
        "important_formulas": summary.important_formulas or [],
        "important_definitions": summary.important_definitions or [],
        "chunk_count": summary.chunk_count,
    }


@router.get("/summary/available")
async def summaries_available(
    user: StudentDep,
    db: DBDep,
    subject: str = Query(max_length=50),
    medium: str = Query(default="en", max_length=2),
    class_num: int | None = Query(default=None),
):
    subj = subject.lower().strip()
    if subj not in VALID_SUBJECTS:
        raise HTTPException(status_code=422, detail="Invalid subject")

    cn = class_num or user.class_num or 5
    repo = SummaryRepository(db)
    chapters = await repo.list_available(cn, subj, medium)
    return {"class_num": cn, "subject": subj, "chapters_with_summaries": chapters}


@router.post("/query", response_model=StudyResponseDTO)
@limiter.limit("30/minute")
async def study_query(
    request: Request,
    body: StudyQueryRequest,
    user: StudentDep,
    db: DBDep,
) -> StudyResponseDTO:
    # Validate subject against allowlist — Pydantic only checks length
    if body.subject.lower().strip() not in VALID_SUBJECTS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid subject")

    # Tier 1 safeguarding scan on INPUT — no exceptions, always runs first
    if tier1_scan(body.query):
        raise HTTPException(
            status_code=451,
            detail=SAFE_REDIRECT_MESSAGE,
        )

    # Also scan history content for any smuggled trigger phrases
    for turn in body.history:
        if tier1_scan(turn.content):
            raise HTTPException(status_code=451, detail=SAFE_REDIRECT_MESSAGE)

    # Detect summary intent — serve pre-computed summary instead of RAG
    if body.chapter and _is_summary_query(body.query):
        repo = SummaryRepository(db)
        summary = await repo.get(
            user.class_num or 5, body.subject.lower().strip(), body.chapter, body.medium
        )
        if not summary and body.medium == "hi":
            summary = await repo.get(
                user.class_num or 5, body.subject.lower().strip(), body.chapter, "en"
            )
        if summary:
            result = _summary_to_study_response(summary)
            # Summaries are LLM-written at ingestion time — scanned like any other output
            if study_output_blocked(result.model_dump()):
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=OUTPUT_BLOCKED_MESSAGE)
            return result

    chunk_repo = ChunkRepository(db)
    service = RetrievalService(chunk_repo, db_session=db)

    try:
        result: StudyResponseDTO = await service.retrieve(
            query=body.query,
            class_num=user.class_num or 5,
            subject=body.subject.lower().strip(),
            chapter=body.chapter,
            mode="study",
            session_history=[m.model_dump() for m in body.history],
            medium=body.medium,
            trace_id=getattr(request.state, "trace_id", None),
        )
    except CircuitOpenError as e:
        raise HTTPException(status_code=503, detail=str(e))

    # Tier 1 safeguarding scan on OUTPUT — AI response is not trusted
    if study_output_blocked(result.model_dump()):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=OUTPUT_BLOCKED_MESSAGE,
        )

    return result


@router.post("/query/stream")
@limiter.limit("30/minute")
async def study_query_stream(
    request: Request,
    body: StudyQueryRequest,
    user: StudentDep,
    db: DBDep,
):
    if body.subject.lower().strip() not in VALID_SUBJECTS:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid subject")

    if tier1_scan(body.query):
        raise HTTPException(status_code=451, detail=SAFE_REDIRECT_MESSAGE)

    for turn in body.history:
        if tier1_scan(turn.content):
            raise HTTPException(status_code=451, detail=SAFE_REDIRECT_MESSAGE)

    if body.chapter and _is_summary_query(body.query):
        repo = SummaryRepository(db)
        summary = await repo.get(
            user.class_num or 5, body.subject.lower().strip(), body.chapter, body.medium
        )
        if not summary and body.medium == "hi":
            summary = await repo.get(
                user.class_num or 5, body.subject.lower().strip(), body.chapter, "en"
            )
        if summary:
            import json
            result = _summary_to_study_response(summary)
            if study_output_blocked(result.model_dump()):
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=OUTPUT_BLOCKED_MESSAGE)
            async def _summary_stream():
                yield f"event: stage\ndata: {{\"stage\": \"cache_hit\"}}\n\n"
                yield f"event: result\ndata: {json.dumps(result.model_dump(), ensure_ascii=False)}\n\n"
                yield f"event: done\ndata: {{}}\n\n"
            return StreamingResponse(_summary_stream(), media_type="text/event-stream")

    chunk_repo = ChunkRepository(db)
    service = RetrievalService(chunk_repo, db_session=db)

    return StreamingResponse(
        service.retrieve_streaming(
            query=body.query,
            class_num=user.class_num or 5,
            subject=body.subject.lower().strip(),
            chapter=body.chapter,
            session_history=[m.model_dump() for m in body.history],
            medium=body.medium,
            trace_id=getattr(request.state, "trace_id", None),
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


_SUMMARY_KEYWORDS_EN = {
    "summary", "summarize", "summarise", "overview", "chapter summary",
    "what is this chapter about", "explain the chapter", "tell me about this chapter",
    "revise", "revision", "recap",
}
_SUMMARY_KEYWORDS_HI = {
    "सारांश", "सार", "अध्याय का सारांश", "समझाओ", "बताओ",
    "रिवीजन", "दोहराओ", "अध्याय के बारे में",
}


def _is_summary_query(query: str) -> bool:
    q = query.lower().strip()
    for kw in _SUMMARY_KEYWORDS_EN | _SUMMARY_KEYWORDS_HI:
        if kw in q:
            return True
    return False


def _summary_to_study_response(summary) -> StudyResponseDTO:
    notes_parts = [summary.summary]
    if summary.important_formulas:
        notes_parts.append("\n\n**Important Formulas:**")
        for f in summary.important_formulas:
            notes_parts.append(f"- {f}")
    if summary.important_definitions:
        notes_parts.append("\n\n**Important Definitions:**")
        for d in summary.important_definitions:
            notes_parts.append(f"- {d}")

    return StudyResponseDTO(
        key_points=summary.key_topics,
        notes="\n".join(notes_parts),
        misconceptions=[],
        source_chunks=summary.chunk_count,
        has_math=bool(summary.important_formulas),
    )
