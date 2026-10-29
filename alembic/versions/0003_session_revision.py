"""Protect session updates against lost writes."""
from alembic import op
import sqlalchemy as sa

revision = "0003_session_revision"
down_revision = "0002_rag_chunks"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("learning_sessions", sa.Column("revision", sa.Integer(), nullable=False, server_default="1"))


def downgrade():
    op.drop_column("learning_sessions", "revision")
