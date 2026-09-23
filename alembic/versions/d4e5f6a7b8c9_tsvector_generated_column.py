"""tsvector_generated_column

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-09-22 18:30:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'c3d4e5f6a7b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "postgresql":
        op.execute(
            "ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS tsv_content tsvector "
            "GENERATED ALWAYS AS (to_tsvector('english', coalesce(content, ''))) STORED;"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_chunks_tsv_content ON document_chunks USING gin(tsv_content);"
        )
    else:
        # SQLite / other dialects
        with op.batch_alter_table("document_chunks") as batch_op:
            batch_op.add_column(sa.Column("tsv_content", sa.Text(), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_chunks_tsv_content;")
        op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS tsv_content;")
    else:
        with op.batch_alter_table("document_chunks") as batch_op:
            batch_op.drop_column("tsv_content")
