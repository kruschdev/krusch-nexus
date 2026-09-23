"""heading_path_and_search_traces

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-09-22 17:45:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add heading_path to document_chunks
    op.add_column('document_chunks', sa.Column('heading_path', sa.Text(), nullable=True))

    # 2. Create search_traces table
    op.create_table(
        'search_traces',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('workspace_id', sa.Integer(), nullable=False),
        sa.Column('query_hash', sa.String(length=64), nullable=False),
        sa.Column('dense_ranks', sa.Text(), nullable=True),
        sa.Column('sparse_ranks', sa.Text(), nullable=True),
        sa.Column('fused_ranks', sa.Text(), nullable=True),
        sa.Column('boosts_applied', sa.Text(), nullable=True),
        sa.Column('duration_ms', sa.Float(), nullable=True, server_default='0.0'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_search_traces_id', 'search_traces', ['id'], unique=False)
    op.create_index('ix_search_traces_workspace_id', 'search_traces', ['workspace_id'], unique=False)
    op.create_index('ix_search_traces_query_hash', 'search_traces', ['query_hash'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_search_traces_query_hash', table_name='search_traces')
    op.drop_index('ix_search_traces_workspace_id', table_name='search_traces')
    op.drop_index('ix_search_traces_id', table_name='search_traces')
    op.drop_table('search_traces')
    op.drop_column('document_chunks', 'heading_path')
