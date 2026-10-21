"""refresh tokens; notes.user_id foreign key

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-21
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("user_id", sa.String(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("family_id", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    op.create_index("ix_refresh_tokens_token_hash", "refresh_tokens", ["token_hash"], unique=True)
    op.create_index("ix_refresh_tokens_family_id", "refresh_tokens", ["family_id"])

    # Notes now belong to a real user. Batch mode so SQLite (tests, CI) can
    # rebuild the table; on Postgres it is a plain ALTER TABLE.
    with op.batch_alter_table("notes") as batch:
        batch.create_foreign_key("fk_notes_user_id_users", "users", ["user_id"], ["id"], ondelete="CASCADE")


def downgrade() -> None:
    with op.batch_alter_table("notes") as batch:
        batch.drop_constraint("fk_notes_user_id_users", type_="foreignkey")
    op.drop_index("ix_refresh_tokens_family_id", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_token_hash", table_name="refresh_tokens")
    op.drop_index("ix_refresh_tokens_user_id", table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
