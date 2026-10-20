"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-14
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), autoincrement=False, primary_key=True),
        sa.Column("username", sa.String(64)),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("registered_at", TS, nullable=False),
        sa.Column("dm_started", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("total_matches", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("wins", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("losses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("damage_dealt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("damage_received", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("knockouts", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("wins >= 0 AND losses >= 0 AND total_matches >= 0", name="ck_users_counts_nonneg"),
    )
    op.create_index("ix_users_wins", "users", ["wins"])

    op.create_table(
        "matches",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("group_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("group_title", sa.String(256)),
        sa.Column("challenger_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("opponent_id", sa.BigInteger(), sa.ForeignKey("users.id")),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("phase", sa.String(16)),
        sa.Column("active_player_id", sa.BigInteger()),
        sa.Column("turn_number", sa.Integer(), nullable=False),
        sa.Column("pending_move", sa.String(16)),
        sa.Column("deadline_at", TS),
        sa.Column("winner_id", sa.BigInteger(), sa.ForeignKey("users.id")),
        sa.Column("end_reason", sa.String(24)),
        sa.Column("cancel_requested_by", sa.BigInteger()),
        sa.Column("challenge_message_id", sa.Integer()),
        sa.Column("scoreboard_message_id", sa.Integer()),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("completed_at", TS),
        sa.CheckConstraint(
            "status IN ('pending','waiting_dm','active','paused','completed','cancelled','expired')",
            name="ck_matches_status"),
    )
    op.create_index("ix_matches_group_chat_id", "matches", ["group_chat_id"])
    op.create_index("ix_matches_status_deadline", "matches", ["status", "deadline_at"])

    op.create_table(
        "match_players",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("match_id", sa.Integer(), sa.ForeignKey("matches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("hp", sa.Integer(), nullable=False),
        sa.Column("finishers_used", sa.Integer(), nullable=False),
        sa.Column("reversals_used", sa.Integer(), nullable=False),
        sa.Column("last_attack", sa.String(16)),
        sa.Column("prompt_message_id", sa.Integer()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.UniqueConstraint("match_id", "user_id", name="uq_match_player"),
        sa.CheckConstraint("hp >= 0 AND hp <= 100", name="ck_match_players_hp"),
        sa.CheckConstraint("finishers_used >= 0 AND finishers_used <= 2", name="ck_match_players_finishers"),
        sa.CheckConstraint("reversals_used >= 0 AND reversals_used <= 4", name="ck_match_players_reversals"),
    )
    op.create_index("ix_match_players_match_id", "match_players", ["match_id"])
    op.create_index("ix_match_players_user_id", "match_players", ["user_id"])
    op.create_index("uq_match_players_one_active_per_user", "match_players", ["user_id"], unique=True,
                    postgresql_where=sa.text("is_active"))

    op.create_table(
        "match_turns",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("match_id", sa.Integer(), sa.ForeignKey("matches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("turn_number", sa.Integer(), nullable=False),
        sa.Column("attacker_id", sa.BigInteger(), nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=False),
        sa.Column("move", sa.String(16), nullable=False),
        sa.Column("original_damage", sa.Integer(), nullable=False),
        sa.Column("reversed", sa.Boolean(), nullable=False),
        sa.Column("damage_to_target", sa.Integer(), nullable=False),
        sa.Column("damage_to_attacker", sa.Integer(), nullable=False),
        sa.Column("attacker_hp_after", sa.Integer(), nullable=False),
        sa.Column("target_hp_after", sa.Integer(), nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.UniqueConstraint("match_id", "turn_number", name="uq_match_turn_number"),
    )
    op.create_index("ix_match_turns_match_id", "match_turns", ["match_id"])

    op.create_table(
        "media_assets",
        sa.Column("event_type", sa.String(32), primary_key=True),
        sa.Column("local_path", sa.String(256), nullable=False),
        sa.Column("telegram_file_id", sa.String(256)),
    )
    op.create_table(
        "bot_settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.String(128), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("bot_settings")
    op.drop_table("media_assets")
    op.drop_table("match_turns")
    op.drop_table("match_players")
    op.drop_table("matches")
    op.drop_table("users")
