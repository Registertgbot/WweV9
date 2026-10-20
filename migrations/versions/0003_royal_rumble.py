"""royal rumble mode

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-15
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rumbles",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("group_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("group_title", sa.String(256)),
        sa.Column("host_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("phase", sa.String(16)),
        sa.Column("active_player_id", sa.BigInteger()),
        sa.Column("turn_number", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_actor_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pending_move", sa.String(16)),
        sa.Column("pending_target_id", sa.BigInteger()),
        sa.Column("deadline_at", sa.DateTime(timezone=True)),
        sa.Column("next_entry_at", sa.DateTime(timezone=True)),
        sa.Column("winner_id", sa.BigInteger(), sa.ForeignKey("users.id")),
        sa.Column("end_reason", sa.String(24)),
        sa.Column("lobby_message_id", sa.Integer()),
        sa.Column("board_message_id", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("status IN ('lobby','active','completed','cancelled')", name="ck_rumbles_status"),
    )
    op.create_index("ix_rumbles_group_chat_id", "rumbles", ["group_chat_id"])
    op.create_index("ix_rumbles_status_deadline", "rumbles", ["status", "deadline_at"])
    op.create_table(
        "rumble_players",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("rumble_id", sa.Integer(), sa.ForeignKey("rumbles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("entry_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("hp", sa.Integer(), nullable=False, server_default="150"),
        sa.Column("in_ring", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("eliminated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("placement", sa.Integer()),
        sa.Column("eliminations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("damage_dealt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("finishers_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reversals_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_attack", sa.String(16)),
        sa.Column("afk_strikes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prompt_message_id", sa.Integer()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.UniqueConstraint("rumble_id", "user_id", name="uq_rumble_player"),
        sa.CheckConstraint("hp >= 0 AND hp <= 150", name="ck_rumble_players_hp"),
    )
    op.create_index("ix_rumble_players_rumble_id", "rumble_players", ["rumble_id"])
    op.create_index("ix_rumble_players_user_id", "rumble_players", ["user_id"])
    op.create_index("uq_rumble_players_one_active_per_user", "rumble_players", ["user_id"], unique=True,
                    postgresql_where=sa.text("is_active"), sqlite_where=sa.text("is_active"))


def downgrade() -> None:
    op.drop_table("rumble_players")
    op.drop_table("rumbles")
