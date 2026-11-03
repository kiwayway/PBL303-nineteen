"""add logo and background images to categories"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("categories", sa.Column("logo_image", sa.String(length=255), nullable=True))
    op.add_column("categories", sa.Column("background_image", sa.String(length=255), nullable=True))


def downgrade():
    op.drop_column("categories", "background_image")
    op.drop_column("categories", "logo_image")
