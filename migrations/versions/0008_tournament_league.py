"""tournament league format (round-robin + top-4 playoffs)

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-19
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tournaments", sa.Column("format", sa.String(10), nullable=False, server_default="knockout"))
    op.add_column("tournaments", sa.Column("league_rounds", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("tournament_fixtures", sa.Column("margin", sa.Integer()))


def downgrade() -> None:
    op.drop_column("tournament_fixtures", "margin")
    op.drop_column("tournaments", "league_rounds")
    op.drop_column("tournaments", "format")
