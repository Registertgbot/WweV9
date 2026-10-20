"""solo tournament (owner-run knockout with IST fixtures)

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-18
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column("matches", sa.Column("fixture_id", sa.Integer()))
    op.create_index("ix_matches_fixture_id", "matches", ["fixture_id"])
    op.create_table(
        "tournaments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("group_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("group_title", sa.String(256)),
        sa.Column("owner_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(64), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="registration"),
        sa.Column("max_players", sa.Integer(), nullable=False, server_default="16"),
        sa.Column("bracket_size", sa.Integer()),
        sa.Column("total_rounds", sa.Integer()),
        sa.Column("reg_close_at", TS),
        sa.Column("start_at", TS),
        sa.Column("round_gap_min", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("match_gap_min", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("noshow_sec", sa.Integer(), nullable=False, server_default="300"),
        sa.Column("paused", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("winner_id", sa.BigInteger(), sa.ForeignKey("users.id")),
        sa.Column("registration_message_id", sa.Integer()),
        sa.Column("bracket_message_id", sa.Integer()),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("started_at", TS),
        sa.Column("completed_at", TS),
        sa.CheckConstraint("status IN ('registration','active','completed','cancelled')", name="ck_tournaments_status"),
    )
    op.create_index("ix_tournaments_group_chat_id", "tournaments", ["group_chat_id"])
    op.create_index("uq_tournaments_one_open_per_chat", "tournaments", ["group_chat_id"], unique=True,
                    postgresql_where=sa.text("status IN ('registration','active')"),
                    sqlite_where=sa.text("status IN ('registration','active')"))
    op.create_table(
        "tournament_players",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tournament_id", sa.Integer(), sa.ForeignKey("tournaments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.String(12), nullable=False, server_default="registered"),
        sa.Column("seed", sa.Integer()),
        sa.Column("out_round", sa.Integer()),
        sa.Column("joined_at", TS, nullable=False),
        sa.Column("wins", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("losses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.UniqueConstraint("tournament_id", "user_id", name="uq_tournament_player"),
    )
    op.create_index("ix_tournament_players_tournament_id", "tournament_players", ["tournament_id"])
    op.create_index("ix_tournament_players_user_id", "tournament_players", ["user_id"])
    op.create_index("uq_tournament_players_one_active_per_user", "tournament_players", ["user_id"], unique=True,
                    postgresql_where=sa.text("is_active"), sqlite_where=sa.text("is_active"))
    op.create_table(
        "tournament_fixtures",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tournament_id", sa.Integer(), sa.ForeignKey("tournaments.id", ondelete="CASCADE"), nullable=False),
        sa.Column("round_no", sa.Integer(), nullable=False),
        sa.Column("slot", sa.Integer(), nullable=False),
        sa.Column("p1_id", sa.BigInteger()),
        sa.Column("p2_id", sa.BigInteger()),
        sa.Column("status", sa.String(10), nullable=False, server_default="pending"),
        sa.Column("winner_id", sa.BigInteger()),
        sa.Column("result", sa.String(12)),
        sa.Column("scheduled_at", TS),
        sa.Column("time_locked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("reminded", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("blocked_since", TS),
        sa.Column("match_id", sa.Integer()),
        sa.Column("started_at", TS),
        sa.Column("finished_at", TS),
        sa.UniqueConstraint("tournament_id", "round_no", "slot", name="uq_tournament_fixture_slot"),
        sa.CheckConstraint("status IN ('pending','scheduled','live','done')", name="ck_tournament_fixtures_status"),
    )
    op.create_index("ix_tournament_fixtures_tournament_id", "tournament_fixtures", ["tournament_id"])
    op.create_index("ix_tournament_fixtures_status_time", "tournament_fixtures", ["status", "scheduled_at"])


def downgrade() -> None:
    for table in ("tournament_fixtures", "tournament_players", "tournaments"):
        op.drop_table(table)
    op.drop_index("ix_matches_fixture_id", table_name="matches")
    op.drop_column("matches", "fixture_id")
