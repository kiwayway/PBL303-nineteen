"""Snapshot item pesanan saat checkout.

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("orders", sa.Column("customer_note", sa.String(length=255), nullable=True))
    op.add_column("order_items", sa.Column("service_title", sa.String(length=160), nullable=True))
    op.add_column("order_items", sa.Column("package_name", sa.String(length=80), nullable=True))
    op.add_column("order_items", sa.Column("duration_days", sa.Integer(), nullable=True))
    op.execute("""
        UPDATE order_items
        SET service_title = (SELECT services.title FROM service_packages JOIN services ON services.id=service_packages.service_id WHERE service_packages.id=order_items.package_id),
            package_name = (SELECT service_packages.name FROM service_packages WHERE service_packages.id=order_items.package_id),
            duration_days = (SELECT service_packages.duration_days FROM service_packages WHERE service_packages.id=order_items.package_id)
    """)

def downgrade():
    op.drop_column("order_items", "duration_days")
    op.drop_column("order_items", "package_name")
    op.drop_column("order_items", "service_title")
    op.drop_column("orders", "customer_note")
