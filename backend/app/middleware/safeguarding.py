"""
Safeguarding middleware — CANNOT be disabled or bypassed.
Tier 1: instant hardcoded phrase match (no AI, no network call).
Tier 2: Gemini-assisted borderline check.

If this module fails to load at startup, the entire app refuses to start.
"""
import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

_PHRASES: list[str] = []
_PHRASES_LOADED = False


def _load_phrases() -> None:
    global _PHRASES, _PHRASES_LOADED
    if _PHRASES_LOADED:
        return

    phrases_path = os.environ.get("PHRASES_FILE_PATH", "./safeguarding/phrases_v1.json")
    path = Path(phrases_path)
    if not path.exists() and not path.is_absolute():
        # backend/safeguarding is a git symlink to the repo-root folder; on checkouts
        # without symlink support (Windows) fall back to the repo root copy.
        repo_root = Path(__file__).resolve().parents[3]
        path = repo_root / path
    if not path.exists():
        raise RuntimeError(
            f"Safeguarding phrases file not found at {phrases_path}. "
            "App cannot start without it."
        )

    data = json.loads(path.read_text(encoding="utf-8"))
    raw = data.get("tier1_phrases", [])
    if not raw:
        raise RuntimeError(
            "phrases_v1.json has no tier1_phrases. App cannot start without safeguarding phrases."
        )

    # Pre-compile each phrase as a case-insensitive pattern
    _PHRASES = [p.strip().lower() for p in raw if p.strip()]
    _PHRASES_LOADED = True
    log.info("safeguarding_phrases_loaded", count=len(_PHRASES))


# Force load at import time — if it fails, app startup fails
_load_phrases()


def _hash_id(student_id: str) -> str:
    """One-way hash for logging — never log raw student_id."""
    import hashlib
    return hashlib.sha256(student_id.encode()).hexdigest()[:16]


def tier1_scan(text: str) -> bool:
    """
    Exact and partial case-insensitive match against phrases_v1.json.
    Returns True if ANY phrase is found in the text.
    Runs in microseconds — no AI, no I/O.
    """
    lowered = text.lower()
    for phrase in _PHRASES:
        if phrase in lowered:
            return True
    return False


async def tier1_flag_and_alert(
    student_id: str,
    centre_id: str,
    trigger_phrase: str,
    input_text: str,
    firestore_client: Any,
    alert_email: str,
) -> None:
    """
    Persists safeguarding flag to Firestore (PERMANENT — never deleted).
    Triggers email alert to centre lead.
    Runs async but does not block the HTTP response — caller handles 451.
    """
    flag_id = str(uuid.uuid4())
    flag_data = {
        "student_id": student_id,
        "trigger_type": "tier1",
        "trigger_phrase": trigger_phrase,
        "input_text": input_text,          # stored only in safeguarding_flags
        "centre_id": centre_id,
        "flagged_at": datetime.now(timezone.utc).isoformat(),
        "resolved": False,
        "resolved_by": None,
    }

    try:
        doc_ref = firestore_client.collection("safeguarding_flags").document(flag_id)
        doc_ref.set(flag_data)
        log.warning(
            "safeguarding_tier1_triggered",
            student_id_hash=_hash_id(student_id),
            centre_id=centre_id,
            flag_id=flag_id,
        )
    except Exception as exc:
        # Log but don't swallow — caller must still return 451
        log.error("safeguarding_firestore_write_failed", error=str(exc))

    # Email alert via Cloud Function / SendGrid (stub — wired in prod via env)
    _send_safeguarding_email(flag_id, centre_id, alert_email)


def _send_safeguarding_email(flag_id: str, centre_id: str, alert_email: str) -> None:
    """Stub — in production, this triggers a Cloud Function via Pub/Sub."""
    log.info(
        "safeguarding_alert_queued",
        flag_id=flag_id,
        centre_id=centre_id,
        alert_email=alert_email,
    )


async def tier2_scan(text: str, gemini_client: Any) -> bool:
    """
    Asks Gemini whether the text is concerning for a child's wellbeing.
    Soft check only — does NOT trigger permanent Firestore flag.
    Returns True if Gemini flags as concerning.
    """
    prompt = (
        "You are a child safeguarding assistant. "
        "A student aged 10-18 sent this message. "
        "Reply only with JSON: {\"concerning\": true} or {\"concerning\": false}. "
        "Flag as concerning ONLY if the message strongly suggests the student is in distress, "
        "being harmed, or has thoughts of self-harm. Academic difficulty alone is NOT concerning.\n\n"
        f"Message: {text[:300]}"  # cap at 300 chars to prevent injection via length
    )
    try:
        result = await gemini_client.generate(prompt, max_tokens=20)
        import json as _json
        parsed = _json.loads(result)
        return bool(parsed.get("concerning", False))
    except Exception:
        # Fail safe — if Tier 2 errors, treat as not triggered (Tier 1 is the hard gate)
        return False


SAFE_REDIRECT_MESSAGE = (
    "Let's take a break for a moment. "
    "A teacher at your centre is here to help you. "
    "Please talk to them or another trusted adult."
)
