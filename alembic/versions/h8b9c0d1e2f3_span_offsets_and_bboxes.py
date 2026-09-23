"""span_offsets_and_bboxes

Revision ID: h8b9c0d1e2f3
Revises: g7a8b9c0d1e2
Create Date: 2026-09-22 20:45:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'h8b9c0d1e2f3'
down_revision: Union[str, Sequence[str], None] = 'g7a8b9c0d1e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('document_chunks', sa.Column('char_start', sa.Integer(), nullable=True))
    op.add_column('document_chunks', sa.Column('char_end', sa.Integer(), nullable=True))
    op.add_column('document_chunks', sa.Column('bbox', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('document_chunks', 'bbox')
    op.drop_column('document_chunks', 'char_end')
    op.drop_column('document_chunks', 'char_start')
