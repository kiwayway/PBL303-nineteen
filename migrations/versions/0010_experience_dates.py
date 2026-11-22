"""Periode pengalaman memakai tanggal mulai dan selesai.

Kolom `period` (teks) lama tidak dihapus supaya data lama tetap tampil.
"""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("experiences", sa.Column("start_date", sa.Date(), nullable=True))
    op.add_column("experiences", sa.Column("end_date", sa.Date(), nullable=True))


def downgrade():
    with op.batch_alter_table("experiences") as batch:
        batch.drop_column("end_date")
        batch.drop_column("start_date")