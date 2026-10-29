"""Join roadmap cache and session revision branches without changing stored data."""
revision = "0004_merge_heads"
down_revision = ("0003", "0003_session_revision")
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
