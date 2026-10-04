"""production hardening — response cache, prompt versioning, new trace columns

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-19 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from pgvector.sqlalchemy import Vector

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _create_rag_traces_base() -> None:
    """rag_traces was originally created by the app's create_all() at startup,
    so on a fresh database it does not exist yet. Create the pre-0002 shape."""
    op.create_table(
        "rag_traces",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("trace_id", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("mode", sa.Text, nullable=False),
        sa.Column("subject", sa.Text, nullable=False),
        sa.Column("class_num", sa.Integer, nullable=False),
        sa.Column("chapter", sa.Text, nullable=True),
        sa.Column("medium", sa.Text, nullable=False, server_default="en"),
        sa.Column("embed_ms", sa.Float, nullable=False),
        sa.Column("vector_hits", sa.Integer, nullable=False),
        sa.Column("vector_top_score", sa.Float, nullable=False),
        sa.Column("bm25_hits", sa.Integer, nullable=False),
        sa.Column("bm25_top_score", sa.Float, nullable=False),
        sa.Column("retrieval_ms", sa.Float, nullable=False),
        sa.Column("lang_fallback", sa.Boolean, nullable=False),
        sa.Column("rrf_chunks", sa.Integer, nullable=False),
        sa.Column("chunk_chapters", postgresql.ARRAY(sa.Text), nullable=True),
        sa.Column("llm_provider", sa.Text, nullable=False, server_default=""),
        sa.Column("llm_model", sa.Text, nullable=False, server_default=""),
        sa.Column("llm_prompt_tokens", sa.Integer, nullable=False),
        sa.Column("llm_completion_tokens", sa.Integer, nullable=False),
        sa.Column("llm_total_tokens", sa.Integer, nullable=False),
        sa.Column("llm_ms", sa.Float, nullable=False),
        sa.Column("llm_json_valid", sa.Boolean, nullable=False),
        sa.Column("empty_response", sa.Boolean, nullable=False),
        sa.Column("mcq_generated", sa.Integer, nullable=False),
        sa.Column("mcq_dropped", sa.Integer, nullable=False),
        sa.Column("ground_check_kept", sa.Integer, nullable=False),
        sa.Column("ground_check_dropped", sa.Integer, nullable=False),
        sa.Column("total_ms", sa.Float, nullable=False),
        sa.Column("error", sa.Text, nullable=True),
    )
    op.create_index("ix_rag_traces_trace_id", "rag_traces", ["trace_id"])
    op.create_index("ix_rag_traces_created_at", "rag_traces", ["created_at"])
    op.create_index("ix_rag_traces_subject_mode", "rag_traces", ["subject", "mode"])


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("rag_traces"):
        _create_rag_traces_base()
        existing = set()
    else:
        existing = {c["name"] for c in inspector.get_columns("rag_traces")}

    # -- rag_traces: add columns that were previously ALTER TABLE'd + new ones --
    new_columns = [
        sa.Column("query_text", sa.Text, nullable=True),
        sa.Column("retrieved_chunks_preview", postgresql.ARRAY(sa.Text), nullable=True),
        sa.Column("llm_response_preview", sa.Text, nullable=True),
        sa.Column("prompt_version", sa.Text, nullable=True),
        sa.Column("cache_hit", sa.Boolean, nullable=False, server_default="false"),
    ]
    for column in new_columns:
        if column.name not in existing:
            op.add_column("rag_traces", column)

    # -- response_cache table --
    op.create_table(
        "response_cache",
        sa.Column("cache_id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("class_num", sa.Integer, nullable=False),
        sa.Column("subject", sa.Text, nullable=False),
        sa.Column("chapter", sa.Text, nullable=True),
        sa.Column("medium", sa.Text, nullable=False, server_default="en"),
        sa.Column("query_text", sa.Text, nullable=False),
        sa.Column("query_embedding", Vector(768), nullable=False),
        sa.Column("response_json", postgresql.JSONB, nullable=False),
        sa.Column("prompt_version", sa.Text, nullable=False),
        sa.Column("hit_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_hit_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ttl_hours", sa.Integer, nullable=False, server_default="168"),
    )
    op.create_index("ix_response_cache_lookup", "response_cache", ["class_num", "subject", "chapter"])
    op.create_index("ix_response_cache_created", "response_cache", ["created_at"])


def downgrade() -> None:
    op.drop_table("response_cache")
    op.drop_column("rag_traces", "cache_hit")
    op.drop_column("rag_traces", "prompt_version")
    op.drop_column("rag_traces", "llm_response_preview")
    op.drop_column("rag_traces", "retrieved_chunks_preview")
    op.drop_column("rag_traces", "query_text")
