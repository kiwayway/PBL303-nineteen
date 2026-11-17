"""Pengajuan seller dan rekening pencairan.

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "seller_applications",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("status", sa.String(length=14), nullable=False, server_default="draft"),
        sa.Column("service_plan", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("admin_note", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('draft', 'submitted', 'needs_revision', 'approved', 'rejected')",
            name="ck_seller_applications_status",
        ),
    )
    op.create_index("ix_seller_applications_user_id", "seller_applications", ["user_id"], unique=True)
    op.create_index("ix_seller_applications_status", "seller_applications", ["status"])

    op.create_table(
        "seller_application_categories",
        sa.Column("application_id", sa.Integer(), sa.ForeignKey("seller_applications.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("category_id", sa.Integer(), sa.ForeignKey("categories.id", ondelete="CASCADE"), primary_key=True),
    )

    op.create_table(
        "payout_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("bank_name", sa.String(length=40), nullable=False),
        sa.Column("account_holder", sa.String(length=120), nullable=False),
        sa.Column("account_number_enc", sa.Text(), nullable=False),
        sa.Column("account_last4", sa.String(length=4), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_payout_accounts_user_id", "payout_accounts", ["user_id"], unique=True)


def downgrade():
    op.drop_index("ix_payout_accounts_user_id", table_name="payout_accounts")
    op.drop_table("payout_accounts")
    op.drop_table("seller_application_categories")
    op.drop_index("ix_seller_applications_status", table_name="seller_applications")
    op.drop_index("ix_seller_applications_user_id", table_name="seller_applications")
    op.drop_table("seller_applications")
