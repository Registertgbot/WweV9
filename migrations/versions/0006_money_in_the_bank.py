"""money in the bank tournament

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-16
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "mitb_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("group_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("group_title", sa.String(256)),
        sa.Column("host_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("mode", sa.String(6), nullable=False, server_default="fast"),
        sa.Column("capacity", sa.Integer(), nullable=False, server_default="8"),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("round_no", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("phase", sa.String(16)),
        sa.Column("turn_number", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ladder_turns", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active_player_id", sa.BigInteger()),
        sa.Column("pending_move", sa.String(16)),
        sa.Column("pending_target_id", sa.BigInteger()),
        sa.Column("deadline_at", TS),
        sa.Column("winner_id", sa.BigInteger(), sa.ForeignKey("users.id")),
        sa.Column("end_reason", sa.String(24)),
        sa.Column("lobby_message_id", sa.Integer()),
        sa.Column("board_message_id", sa.Integer()),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("started_at", TS),
        sa.Column("completed_at", TS),
        sa.CheckConstraint("status IN ('lobby','qualifier','ladder','completed','cancelled')", name="ck_mitb_events_status"),
    )
    op.create_index("ix_mitb_events_group_chat_id", "mitb_events", ["group_chat_id"])
    op.create_index("ix_mitb_events_status_deadline", "mitb_events", ["status", "deadline_at"])
    op.create_table(
        "mitb_players",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("mitb_events.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(10), nullable=False, server_default="alive"),
        sa.Column("had_bye", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("out_round", sa.Integer()),
        sa.Column("rung", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("hp", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("finishers_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reversals_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bonus_reversals", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_attack", sa.String(16)),
        sa.Column("afk_strikes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stun", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("damage_dealt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ladder_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prompt_message_id", sa.Integer()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.UniqueConstraint("event_id", "user_id", name="uq_mitb_player"),
        sa.CheckConstraint("hp >= 0 AND hp <= 100", name="ck_mitb_players_hp"),
        sa.CheckConstraint("rung >= 0 AND rung <= 5", name="ck_mitb_players_rung"),
    )
    op.create_index("ix_mitb_players_event_id", "mitb_players", ["event_id"])
    op.create_index("ix_mitb_players_user_id", "mitb_players", ["user_id"])
    op.create_index("uq_mitb_players_one_active_per_user", "mitb_players", ["user_id"], unique=True,
                    postgresql_where=sa.text("is_active"), sqlite_where=sa.text("is_active"))
    op.create_table(
        "briefcases",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("group_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("event_id", sa.Integer()),
        sa.Column("status", sa.String(8), nullable=False, server_default="active"),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("used_at", TS),
    )
    op.create_index("ix_briefcases_user_id", "briefcases", ["user_id"])
    op.create_index("ix_briefcases_group_chat_id", "briefcases", ["group_chat_id"])
    op.create_index("uq_briefcases_one_active_per_user", "briefcases", ["user_id"], unique=True,
                    postgresql_where=sa.text("status = 'active'"), sqlite_where=sa.text("status = 'active'"))
    op.create_table(
        "mitb_duels",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("mitb_events.id", ondelete="CASCADE")),
        sa.Column("group_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False, server_default="bracket"),
        sa.Column("mode", sa.String(6), nullable=False, server_default="fast"),
        sa.Column("round_no", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("briefcase_id", sa.Integer(), sa.ForeignKey("briefcases.id")),
        sa.Column("status", sa.String(10), nullable=False, server_default="active"),
        sa.Column("phase", sa.String(16)),
        sa.Column("turn_number", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active_user_id", sa.BigInteger()),
        sa.Column("pending_move", sa.String(16)),
        sa.Column("deadline_at", TS),
        sa.Column("cap_at", TS),
        sa.Column("winner_id", sa.BigInteger()),
        sa.Column("end_reason", sa.String(24)),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("completed_at", TS),
    )
    op.create_index("ix_mitb_duels_event_id", "mitb_duels", ["event_id"])
    op.create_index("ix_mitb_duels_group_chat_id", "mitb_duels", ["group_chat_id"])
    op.create_index("ix_mitb_duels_status_deadline", "mitb_duels", ["status", "deadline_at"])
    op.create_table(
        "mitb_duel_players",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("duel_id", sa.Integer(), sa.ForeignKey("mitb_duels.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("hp", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("finishers_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reversals_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bonus_reversals", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_attack", sa.String(16)),
        sa.Column("afk_strikes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("damage_dealt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prompt_message_id", sa.Integer()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.UniqueConstraint("duel_id", "user_id", name="uq_mitb_duel_player"),
        sa.CheckConstraint("hp >= 0 AND hp <= 100", name="ck_mitb_duel_players_hp"),
    )
    op.create_index("ix_mitb_duel_players_duel_id", "mitb_duel_players", ["duel_id"])
    op.create_index("ix_mitb_duel_players_user_id", "mitb_duel_players", ["user_id"])
    op.create_index("uq_mitb_duel_players_one_active_per_user", "mitb_duel_players", ["user_id"], unique=True,
                    postgresql_where=sa.text("is_active"), sqlite_where=sa.text("is_active"))
    op.create_table(
        "cashin_offers",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("briefcase_id", sa.Integer(), sa.ForeignKey("briefcases.id"), nullable=False),
        sa.Column("group_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("target_user_id", sa.BigInteger(), nullable=False),
        sa.Column("target_hp", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("status", sa.String(8), nullable=False, server_default="open"),
        sa.Column("expires_at", TS, nullable=False),
        sa.Column("message_id", sa.Integer()),
    )
    op.create_index("ix_cashin_offers_briefcase_id", "cashin_offers", ["briefcase_id"])
    op.create_index("ix_cashin_offers_group_chat_id", "cashin_offers", ["group_chat_id"])


def downgrade() -> None:
    for table in ("cashin_offers", "mitb_duel_players", "mitb_duels", "briefcases", "mitb_players", "mitb_events"):
        op.drop_table(table)
