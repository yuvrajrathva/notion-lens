"""add fulltext search

Revision ID: 5c10c1e566d2
Revises: 02ba0896868b
Create Date: 2026-09-12 23:17:52.635910

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = '5c10c1e566d2'
down_revision: Union[str, Sequence[str], None] = '02ba0896868b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # Generated column kept automatically in sync with `content` (breadcrumb +
    # body) on every insert/update; no application-side write path needed.
    # Alembic has no declarative op for generated columns, so this is raw SQL -
    # same pattern as the HNSW expression index in the initial migration.
    op.execute(
        "ALTER TABLE notion_chunks "
        "ADD COLUMN content_tsv tsvector "
        "GENERATED ALWAYS AS (to_tsvector('english', content)) STORED"
    )
    op.execute(
        "CREATE INDEX ix_notion_chunks_content_tsv ON notion_chunks USING gin (content_tsv)"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP INDEX IF EXISTS ix_notion_chunks_content_tsv")
    op.execute("ALTER TABLE notion_chunks DROP COLUMN IF EXISTS content_tsv")
