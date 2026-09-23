"""document_lineage_and_superseded

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-09-22 19:10:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, Sequence[str], None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('documents', sa.Column('version', sa.Integer(), nullable=True, server_default='1'))
    op.add_column('document_chunks', sa.Column('is_superseded', sa.Boolean(), nullable=True, server_default='false'))


def downgrade() -> None:
    op.drop_column('document_chunks', 'is_superseded')
    op.drop_column('documents', 'version')
