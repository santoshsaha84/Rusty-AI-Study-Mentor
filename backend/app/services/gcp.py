"""
Thin adapters for the GCP services used by cloud ingestion — Cloud Storage (textbook PDFs)
and Cloud Run Jobs (rusty-ingest). Only used when TEXTBOOK_BUCKET is set; local development
never imports the Google clients. Authentication is Application Default Credentials
(the Cloud Run service account) — no key files.
"""
import asyncio

import httpx

from app.core.config import get_settings

_CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


def textbook_object_name(textbook_id: str) -> str:
    # Unique per upload: the API only has objectCreator, so objects are never overwritten.
    return f"textbooks/{textbook_id}.pdf"


def _bucket():
    from google.cloud import storage

    settings = get_settings()
    return storage.Client(project=settings.gcp_project_id).bucket(settings.textbook_bucket)


async def upload_textbook_pdf(textbook_id: str, content: bytes) -> None:
    blob = _bucket().blob(textbook_object_name(textbook_id))
    await asyncio.to_thread(blob.upload_from_string, content, content_type="application/pdf")


async def download_textbook_pdf(textbook_id: str, dest_path: str) -> None:
    blob = _bucket().blob(textbook_object_name(textbook_id))
    await asyncio.to_thread(blob.download_to_filename, dest_path)


def _access_token() -> str:
    import google.auth
    from google.auth.transport.requests import Request

    credentials, _ = google.auth.default(scopes=[_CLOUD_PLATFORM_SCOPE])
    credentials.refresh(Request())
    return credentials.token


async def start_ingest_job() -> None:
    """Start one execution of the ingestion job (needs roles/run.jobsExecutor, no overrides).

    The job itself finds work by scanning `textbooks` for status='processing', so no
    per-execution arguments are passed.
    """
    settings = get_settings()
    token = await asyncio.to_thread(_access_token)
    url = (
        f"https://run.googleapis.com/v2/projects/{settings.gcp_project_id}"
        f"/locations/{settings.gcp_region}/jobs/{settings.ingest_job_name}:run"
    )
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, headers={"Authorization": f"Bearer {token}"}, json={})
        resp.raise_for_status()
