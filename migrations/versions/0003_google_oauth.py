"""identitas OAuth (Google) dan phone boleh NULL

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-02
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "oauth_identities",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider", "subject", name="uq_oauth_provider_subject"),
        sa.UniqueConstraint("user_id", "provider", name="uq_oauth_user_provider"),
    )
    op.create_index("ix_oauth_identities_user_id", "oauth_identities", ["user_id"])
    # Akun Google belum punya nomor HP. Indeks unik tetap berlaku untuk nilai terisi
    # (PostgreSQL mengizinkan banyak NULL pada kolom unik).
    op.alter_column("users", "phone", existing_type=sa.String(20), nullable=True)


def downgrade():
    # Gagal bila masih ada user tanpa phone: isi dulu atau hapus akun Google tsb.
    op.alter_column("users", "phone", existing_type=sa.String(20), nullable=False)
    op.drop_index("ix_oauth_identities_user_id", table_name="oauth_identities")
    op.drop_table("oauth_identities")
