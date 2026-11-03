"""create contact inquiries table

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-04
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "contact_inquiries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("subject", sa.String(length=120), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="new", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(name) BETWEEN 2 AND 120", name="ck_contact_name_length"),
        sa.CheckConstraint("length(email) BETWEEN 5 AND 254", name="ck_contact_email_length"),
        sa.CheckConstraint("length(subject) BETWEEN 3 AND 120", name="ck_contact_subject_length"),
        sa.CheckConstraint("length(message) BETWEEN 10 AND 5000", name="ck_contact_message_length"),
    )
    op.create_index("ix_contact_inquiries_email", "contact_inquiries", ["email"])
    op.create_index("ix_contact_inquiries_status", "contact_inquiries", ["status"])
    op.create_index("ix_contact_inquiries_created_at", "contact_inquiries", ["created_at"])


def downgrade():
    op.drop_index("ix_contact_inquiries_created_at", table_name="contact_inquiries")
    op.drop_index("ix_contact_inquiries_status", table_name="contact_inquiries")
    op.drop_index("ix_contact_inquiries_email", table_name="contact_inquiries")
    op.drop_table("contact_inquiries")
