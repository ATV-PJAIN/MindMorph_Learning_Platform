"""Nullable passage provenance; legacy pages/source groupings remain unknown."""
from alembic import op
import sqlalchemy as sa

revision = "0005_grounding_provenance"
down_revision = "0004_merge_heads"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("rag_chunks", sa.Column("source_id", sa.String(), nullable=True))
    op.add_column("rag_chunks", sa.Column("page", sa.Integer(), nullable=True))


def downgrade():
    op.drop_column("rag_chunks", "page")
    op.drop_column("rag_chunks", "source_id")
