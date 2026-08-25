"""essays -- the first generated artifact

Phase 6. Ship 30 essays are written from an answer that already exists, and
they need to outlive the request that produced one: a 1,250-word essay costs
minutes on the local path, so losing it to a page refresh is not acceptable.

Why a table rather than `messages.kind`:

  - an essay is not a turn. It must not appear in the conversation transcript,
    and it must not enter `retrieve_for_session`'s history;
  - the Artifact Viewer needs to address one by id, which a discriminator on a
    conversation row does not give;
  - Phase 7 adds a rendering/isolation policy over this resource. Giving it a
    real resource now costs one table and saves that phase a refactor.

Every trust column `messages` carries is carried here for the same reasons:
`sources` so citations survive a reload, `grounding` so a FAILED verdict still
retracts the essay after a refresh, provider/model so the claim stays
attributable. `grounding` is NULLABLE for the same reason as on messages --
NULL means no verdict was recorded, which is not a recorded PASS.

`skill_name` / `skill_sha256` provenance the INSTRUCTIONS as well as the model:
an essay records the exact revision of SKILL.md that produced it, so a later
change in house style stays attributable.

Purely additive. No existing table is touched, so there is nothing on an
existing deployment for this to break.

Revision ID: 0004
Revises: 0003
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "essays",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        # CASCADE: deleting a session takes its artifacts with it, like its
        # messages. An orphaned essay would cite a conversation that is gone.
        sa.Column("session_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("sessions.id", ondelete="CASCADE"),
                  nullable=False),
        # SET NULL, not CASCADE: deleting one message must not silently
        # destroy a finished artifact. Deleting the session still takes both.
        sa.Column("source_message_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("messages.id", ondelete="SET NULL"),
                  nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.Column("format", sa.String(16), nullable=False,
                  server_default="markdown"),
        sa.Column("word_count", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("sources", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
        sa.Column("grounding", postgresql.JSONB(), nullable=True),
        sa.Column("skill_name", sa.String(64), nullable=False),
        sa.Column("skill_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint("word_count >= 0", name="ck_essays_word_count"),
    )
    op.create_index("ix_essays_session_id", "essays", ["session_id"])
    op.create_index("ix_essays_session_created", "essays",
                    ["session_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_essays_session_created", table_name="essays")
    op.drop_index("ix_essays_session_id", table_name="essays")
    op.drop_table("essays")
