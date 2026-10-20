"""tag team sizes (2v2 / 3v3 / 4v4) and handicap reversals

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-15
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tag_matches", sa.Column("team_size", sa.Integer(), nullable=False, server_default="2"))
    op.add_column("tag_players", sa.Column("bonus_reversals", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("tag_players", "bonus_reversals")
    op.drop_column("tag_matches", "team_size")
