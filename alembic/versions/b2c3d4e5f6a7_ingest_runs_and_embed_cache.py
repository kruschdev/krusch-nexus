"""ingest_runs_and_embed_cache

Revision ID: b2c3d4e5f6a7
Revises: 7a1b2c3d4e5f
Create Date: 2026-09-22 17:15:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = '7a1b2c3d4e5f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Update documents table
    op.add_column('documents', sa.Column('detected_mime', sa.String(length=100), nullable=True, server_default='application/octet-stream'))
    op.add_column('documents', sa.Column('parser_name', sa.String(length=100), nullable=True, server_default='default'))
    op.add_column('documents', sa.Column('ocr_confidence', sa.Text(), nullable=True))

    # 2. Create ingest_runs table
    op.create_table(
        'ingest_runs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('document_id', sa.Integer(), nullable=True),
        sa.Column('workspace_id', sa.Integer(), nullable=True),
        sa.Column('workspace', sa.String(length=255), nullable=True),
        sa.Column('workspace_name', sa.String(length=255), nullable=True),
        sa.Column('filename', sa.String(length=512), nullable=False),
        sa.Column('file_hash', sa.String(length=64), nullable=False),
        sa.Column('state', sa.String(length=50), nullable=False, server_default='detected'),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('completed_at', sa.DateTime(), nullable=True),
        sa.Column('duration_ms', sa.Float(), nullable=True, server_default='0.0'),
        sa.Column('error_class', sa.String(length=100), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('payload', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['document_id'], ['documents.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ingest_runs_document_id'), 'ingest_runs', ['document_id'], unique=False)
    op.create_index(op.f('ix_ingest_runs_workspace_id'), 'ingest_runs', ['workspace_id'], unique=False)
    op.create_index(op.f('ix_ingest_runs_file_hash'), 'ingest_runs', ['file_hash'], unique=False)
    op.create_index(op.f('ix_ingest_runs_state'), 'ingest_runs', ['state'], unique=False)
    op.create_index(op.f('ix_ingest_runs_id'), 'ingest_runs', ['id'], unique=False)

    # 3. Create embed_cache table
    op.create_table(
        'embed_cache',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('text_hash', sa.String(length=64), nullable=False),
        sa.Column('model', sa.String(length=100), nullable=False, server_default='bge-large'),
        sa.Column('dim', sa.Integer(), nullable=True, server_default='1024'),
        sa.Column('vector', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('text_hash', 'model', name='uq_embed_cache_hash_model')
    )
    op.create_index(op.f('ix_embed_cache_text_hash'), 'embed_cache', ['text_hash'], unique=False)
    op.create_index(op.f('ix_embed_cache_model'), 'embed_cache', ['model'], unique=False)
    op.create_index('ix_embed_cache_hash_model', 'embed_cache', ['text_hash', 'model'], unique=False)


def downgrade() -> None:
    op.drop_table('embed_cache')
    op.drop_table('ingest_runs')
    op.drop_column('documents', 'ocr_confidence')
    op.drop_column('documents', 'parser_name')
    op.drop_column('documents', 'detected_mime')
