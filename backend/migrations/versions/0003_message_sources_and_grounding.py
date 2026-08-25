"""message sources and grounding verdict

Phase 5. Until now `sources` and `grounding` were streamed to the client and
then thrown away: `messages` recorded provider/model/latency but nothing about
the evidence an answer was built from, or whether verification passed.

That made reload lossy in exactly the wrong direction. A reopened session
replayed the assistant's text with no citations and -- worse -- with any
FAILED verdict silently dropped, so a retracted answer came back looking
clean. Persisting both is what makes the trust property survive a refresh.

Both columns are additive and safe on existing rows:

  - `sources` is NOT NULL with a '[]' default, so historical rows read as
    "no citations recorded" rather than NULL-checking at every call site.
  - `grounding` is NULLABLE on purpose. NULL means "no verdict was recorded"
    (a pre-Phase-5 row, or a user turn), which is genuinely different from
    a recorded FAIL. Collapsing those two would let an unverified answer
    masquerade as a verified one.

Revision ID: 0003
Revises: 0002
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("sources", postgresql.JSONB(), nullable=False,
                  server_default=sa.text("'[]'::jsonb")),
    )
    op.add_column(
        "messages",
        sa.Column("grounding", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("messages", "grounding")
    op.drop_column("messages", "sources")
