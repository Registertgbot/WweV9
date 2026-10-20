"""tag team mode

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-15
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tag_matches",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("group_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("group_title", sa.String(256)),
        sa.Column("host_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("phase", sa.String(16)),
        sa.Column("turn_number", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("active_team", sa.Integer()),
        sa.Column("active_player_id", sa.BigInteger()),
        sa.Column("pending_move", sa.String(16)),
        sa.Column("pending_target_id", sa.BigInteger()),
        sa.Column("t1_tag_lock", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("t2_tag_lock", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("deadline_at", sa.DateTime(timezone=True)),
        sa.Column("winner_team", sa.Integer()),
        sa.Column("end_reason", sa.String(24)),
        sa.Column("lobby_message_id", sa.Integer()),
        sa.Column("board_message_id", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('lobby','active','completed','cancelled')", name="ck_tag_matches_status"),
    )
    op.create_index("ix_tag_matches_group_chat_id", "tag_matches", ["group_chat_id"])
    op.create_index("ix_tag_matches_status_deadline", "tag_matches", ["status", "deadline_at"])
    op.create_table(
        "tag_players",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("match_id", sa.Integer(), sa.ForeignKey("tag_matches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("team", sa.Integer(), nullable=False),
        sa.Column("slot", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("in_ring", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("eliminated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("hp", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("finishers_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reversals_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_attack", sa.String(16)),
        sa.Column("afk_strikes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tags_made", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("eliminations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("damage_dealt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prompt_message_id", sa.Integer()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.UniqueConstraint("match_id", "user_id", name="uq_tag_player"),
        sa.CheckConstraint("team IN (1, 2)", name="ck_tag_players_team"),
        sa.CheckConstraint("hp >= 0 AND hp <= 100", name="ck_tag_players_hp"),
    )
    op.create_index("ix_tag_players_match_id", "tag_players", ["match_id"])
    op.create_index("ix_tag_players_user_id", "tag_players", ["user_id"])
    op.create_index("uq_tag_players_one_active_per_user", "tag_players", ["user_id"], unique=True,
                    postgresql_where=sa.text("is_active"), sqlite_where=sa.text("is_active"))


def downgrade() -> None:
    op.drop_table("tag_players")
    op.drop_table("tag_matches")
