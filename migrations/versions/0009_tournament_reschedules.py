"""tournament reschedule requests

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-19
"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tournament_reschedules",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tournament_id", sa.Integer(), sa.ForeignKey("tournaments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("fixture_id", sa.Integer(), nullable=False),
        sa.Column("requester_id", sa.BigInteger(), nullable=False),
        sa.Column("opponent_id", sa.BigInteger(), nullable=False),
        sa.Column("old_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("new_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(10), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("decided_by", sa.BigInteger()),
        sa.CheckConstraint("status IN ('pending','accepted','declined','expired','withdrawn')",
                           name="ck_tournament_reschedules_status"),
    )
    op.create_index("ix_tournament_reschedules_tournament_id", "tournament_reschedules", ["tournament_id"])
    op.create_index("ix_tournament_reschedules_fixture_id", "tournament_reschedules", ["fixture_id"])
    op.create_index("uq_tournament_reschedules_one_pending", "tournament_reschedules", ["fixture_id"], unique=True,
                    postgresql_where=sa.text("status = 'pending'"))


def downgrade() -> None:
    op.drop_index("uq_tournament_reschedules_one_pending", table_name="tournament_reschedules")
    op.drop_index("ix_tournament_reschedules_fixture_id", table_name="tournament_reschedules")
    op.drop_index("ix_tournament_reschedules_tournament_id", table_name="tournament_reschedules")
    op.drop_table("tournament_reschedules")
