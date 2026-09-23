"""enable_row_level_security

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-09-22 18:50:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, Sequence[str], None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "postgresql":
        op.execute("ALTER TABLE documents ENABLE ROW LEVEL SECURITY;")
        op.execute("ALTER TABLE document_chunks ENABLE ROW LEVEL SECURITY;")
        op.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_policies WHERE tablename = 'documents' AND policyname = 'documents_workspace_isolation'
                ) THEN
                    CREATE POLICY documents_workspace_isolation ON documents
                    FOR ALL
                    USING (
                        NULLIF(current_setting('app.current_workspace_id', true), '') IS NULL
                        OR workspace_id = NULLIF(current_setting('app.current_workspace_id', true), '')::int
                    )
                    WITH CHECK (
                        NULLIF(current_setting('app.current_workspace_id', true), '') IS NULL
                        OR workspace_id = NULLIF(current_setting('app.current_workspace_id', true), '')::int
                    );
                END IF;
                IF NOT EXISTS (
                    SELECT 1 FROM pg_policies WHERE tablename = 'document_chunks' AND policyname = 'chunks_workspace_isolation'
                ) THEN
                    CREATE POLICY chunks_workspace_isolation ON document_chunks
                    FOR ALL
                    USING (
                        NULLIF(current_setting('app.current_workspace_id', true), '') IS NULL
                        OR workspace_id = NULLIF(current_setting('app.current_workspace_id', true), '')::int
                    )
                    WITH CHECK (
                        NULLIF(current_setting('app.current_workspace_id', true), '') IS NULL
                        OR workspace_id = NULLIF(current_setting('app.current_workspace_id', true), '')::int
                    );
                END IF;
            END $$;
        """)


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == "postgresql":
        op.execute("DROP POLICY IF EXISTS documents_workspace_isolation ON documents;")
        op.execute("DROP POLICY IF EXISTS chunks_workspace_isolation ON document_chunks;")
        op.execute("ALTER TABLE documents DISABLE ROW LEVEL SECURITY;")
        op.execute("ALTER TABLE document_chunks DISABLE ROW LEVEL SECURITY;")
