"""chunk_provenance_and_versioning

Revision ID: 7a1b2c3d4e5f
Revises: 50a73588923a
Create Date: 2026-09-22 17:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '7a1b2c3d4e5f'
down_revision: Union[str, Sequence[str], None] = '50a73588923a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('documents', sa.Column('chunker_version', sa.String(length=50), nullable=True, server_default='1.0'))
    op.add_column('document_chunks', sa.Column('chunker_version', sa.String(length=50), nullable=True, server_default='1.0'))
    op.add_column('document_chunks', sa.Column('embed_model', sa.String(length=100), nullable=True, server_default='bge-large'))
    op.add_column('document_chunks', sa.Column('confidence', sa.Float(), nullable=True))
    op.add_column('document_chunks', sa.Column('char_start', sa.Integer(), nullable=True))
    op.add_column('document_chunks', sa.Column('char_end', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('document_chunks', 'char_end')
    op.drop_column('document_chunks', 'char_start')
    op.drop_column('document_chunks', 'confidence')
    op.drop_column('document_chunks', 'embed_model')
    op.drop_column('document_chunks', 'chunker_version')
    op.drop_column('documents', 'chunker_version')
