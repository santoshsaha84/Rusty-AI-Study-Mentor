"""textbooks + chapter_summaries tables (previously only created by create_all at startup)

Staging/production schema comes solely from Alembic, so these tables need a migration.
Guarded: local databases already have them from create_all.

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-06 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, ARRAY

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())

    if "textbooks" not in existing:
        op.create_table(
            "textbooks",
            sa.Column("textbook_id", UUID(as_uuid=True), primary_key=True),
            sa.Column("source_pdf", sa.Text, nullable=False, unique=True),
            sa.Column("original_filename", sa.Text, nullable=False),
            sa.Column("class_num", sa.Integer, nullable=False),
            sa.Column("subject", sa.Text, nullable=False),
            sa.Column("chapter", sa.Text, nullable=True),
            sa.Column("language", sa.Text, nullable=False, server_default="en"),
            sa.Column("chunk_count", sa.Integer, nullable=True),
            sa.Column("status", sa.Text, nullable=False),
            sa.Column("error_message", sa.Text, nullable=True),
            sa.Column("uploaded_by", sa.Text, nullable=False),
            sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
        op.create_index("ix_textbooks_class_num", "textbooks", ["class_num"])
        op.create_index("ix_textbooks_subject", "textbooks", ["subject"])

    if "chapter_summaries" not in existing:
        op.create_table(
            "chapter_summaries",
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("class_num", sa.Integer, nullable=False),
            sa.Column("subject", sa.Text, nullable=False),
            sa.Column("chapter", sa.Text, nullable=False),
            sa.Column("language", sa.Text, nullable=False),
            sa.Column("summary", sa.Text, nullable=False),
            sa.Column("key_topics", ARRAY(sa.Text), nullable=False),
            sa.Column("important_formulas", ARRAY(sa.Text), nullable=True),
            sa.Column("important_definitions", ARRAY(sa.Text), nullable=True),
            sa.Column("chunk_count", sa.Integer, nullable=False),
            sa.Column("source_pdf", sa.Text, nullable=False),
            sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("class_num", "subject", "chapter", "language", name="uq_chapter_summary"),
        )
        op.create_index(
            "ix_chapter_summary_lookup", "chapter_summaries", ["class_num", "subject", "chapter", "language"]
        )


def downgrade() -> None:
    op.drop_table("chapter_summaries")
    op.drop_table("textbooks")
