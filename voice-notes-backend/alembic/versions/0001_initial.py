"""users and notes

Revision ID: 0001
Revises:
Create Date: 2026-10-21

Mirrors the schema that `Base.metadata.create_all` produced before Alembic
was introduced. Every statement is IF NOT EXISTS so this runs cleanly on a
database that already has the tables (it then just records the revision).
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        if_not_exists=True,
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True, if_not_exists=True)

    op.create_table(
        "notes",
        sa.Column("id", sa.String(), primary_key=True),
        # No FK to users.id yet: rows saved under the pre-auth dev token have a
        # user_id with no matching user. Clean those up, then add the FK in a
        # later revision.
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("raw_transcript", sa.Text(), nullable=False),
        sa.Column("title", sa.String()),
        sa.Column("summary", sa.Text()),
        sa.Column("key_points", sa.JSON()),
        sa.Column("language", sa.String()),
        sa.Column("created_at", sa.DateTime(timezone=True)),
        if_not_exists=True,
    )
    op.create_index("ix_notes_user_id", "notes", ["user_id"], if_not_exists=True)


def downgrade() -> None:
    op.drop_index("ix_notes_user_id", table_name="notes")
    op.drop_table("notes")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
