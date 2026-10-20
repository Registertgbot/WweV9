"""owner-approved groups

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-19
"""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "allowed_groups",
        sa.Column("chat_id", sa.BigInteger(), primary_key=True, autoincrement=False),
        sa.Column("title", sa.String(256)),
        sa.Column("approved_by", sa.BigInteger(), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("allowed_groups")
