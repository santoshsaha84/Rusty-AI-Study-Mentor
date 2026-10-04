"""chunks: add language + page_num columns used by ingestion and retrieval

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-04 00:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Guarded: some environments had these columns added by hand before this migration.
    existing = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("chunks")}
    if "page_num" not in existing:
        op.add_column("chunks", sa.Column("page_num", sa.Integer, nullable=True))
    if "language" not in existing:
        op.add_column("chunks", sa.Column("language", sa.Text, nullable=False, server_default="en"))

    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chunks_retrieval ON chunks (class_num, subject, language, chapter)"
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_chunks_source_pdf ON chunks (source_pdf)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_chunks_source_pdf")
    op.execute("DROP INDEX IF EXISTS ix_chunks_retrieval")
    op.drop_column("chunks", "language")
    op.drop_column("chunks", "page_num")
