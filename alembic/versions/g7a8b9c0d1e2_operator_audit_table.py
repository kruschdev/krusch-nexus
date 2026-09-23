"""operator_audit_table

Revision ID: g7a8b9c0d1e2
Revises: f6a7b8c9d0e1
Create Date: 2026-09-22 19:35:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'g7a8b9c0d1e2'
down_revision: Union[str, Sequence[str], None] = 'f6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'operator_audits',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('action', sa.String(length=50), nullable=False),
        sa.Column('document_id', sa.Integer(), nullable=True),
        sa.Column('workspace_id', sa.Integer(), nullable=True),
        sa.Column('confirmation_token', sa.String(length=100), nullable=False),
        sa.Column('timestamp', sa.DateTime(), nullable=True),
        sa.Column('details', sa.Text(), nullable=True)
    )
    op.create_index('ix_operator_audits_action', 'operator_audits', ['action'])
    op.create_index('ix_operator_audits_document_id', 'operator_audits', ['document_id'])
    op.create_index('ix_operator_audits_workspace_id', 'operator_audits', ['workspace_id'])
    op.create_index('ix_operator_audits_timestamp', 'operator_audits', ['timestamp'])


def downgrade() -> None:
    op.drop_index('ix_operator_audits_timestamp', table_name='operator_audits')
    op.drop_index('ix_operator_audits_workspace_id', table_name='operator_audits')
    op.drop_index('ix_operator_audits_document_id', table_name='operator_audits')
    op.drop_index('ix_operator_audits_action', table_name='operator_audits')
    op.drop_table('operator_audits')
