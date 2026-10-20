"""wild draw, 50/50 reversals

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-14
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("matches", sa.Column("pending_wild", sa.Boolean(), nullable=False,
                                       server_default=sa.text("false")))
    op.add_column("match_players", sa.Column("bonus_reversals", sa.Integer(), nullable=False,
                                             server_default="0"))
    op.add_column("match_players", sa.Column("wild_draws_used", sa.Integer(), nullable=False,
                                             server_default="0"))
    op.add_column("match_turns", sa.Column("reversal_attempted", sa.Boolean(), nullable=False,
                                           server_default=sa.text("false")))
    op.add_column("match_turns", sa.Column("wild", sa.Boolean(), nullable=False,
                                           server_default=sa.text("false")))


def downgrade() -> None:
    op.drop_column("match_turns", "wild")
    op.drop_column("match_turns", "reversal_attempted")
    op.drop_column("match_players", "wild_draws_used")
    op.drop_column("match_players", "bonus_reversals")
    op.drop_column("matches", "pending_wild")
