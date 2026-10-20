"""player-chosen wrestler pictures

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-19
"""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("user_fighters",
                    sa.Column("user_id", sa.BigInteger(), primary_key=True, autoincrement=False),
                    sa.Column("fighter", sa.String(64), nullable=False))


def downgrade() -> None:
    op.drop_table("user_fighters")
