"""championship belts and title matches

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-20
"""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "championships",
        sa.Column("key", sa.String(16), primary_key=True),
        sa.Column("holder1_id", sa.BigInteger()),
        sa.Column("holder2_id", sa.BigInteger()),
        sa.Column("since", sa.DateTime(timezone=True)),
        sa.Column("reigns", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("defenses", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "title_matches",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("belt", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(6), nullable=False),
        sa.Column("match_id", sa.Integer(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("resolved", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_title_matches_open", "title_matches", ["resolved", "belt"])


def downgrade() -> None:
    op.drop_index("ix_title_matches_open", table_name="title_matches")
    op.drop_table("title_matches")
    op.drop_table("championships")
