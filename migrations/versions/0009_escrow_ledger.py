"""Escrow: snapshot komisi, siklus pengerjaan, pembayaran Midtrans, dan ledger.

Revision ID: 0009
Revises: 0008
"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("orders", sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True))

    op.add_column("order_items", sa.Column("fee_bps", sa.Integer(), nullable=False, server_default="1000"))
    op.add_column("order_items", sa.Column("platform_fee", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("order_items", sa.Column("seller_net", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("order_items", sa.Column("due_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("order_items", sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("order_items", sa.Column("auto_complete_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("order_items", sa.Column("delivery_note", sa.String(length=1000), nullable=True))
    op.add_column("order_items", sa.Column("delivery_url", sa.String(length=255), nullable=True))
    op.drop_constraint("ck_order_items_status", "order_items", type_="check")
    op.create_check_constraint(
        "ck_order_items_status", "order_items", "status IN ('in_progress', 'delivered', 'completed')"
    )

    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("midtrans_order_id", sa.String(length=50), nullable=False),
        sa.Column("gross_amount", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=8), nullable=False, server_default="pending"),
        sa.Column("snap_token", sa.String(length=64), nullable=True),
        sa.Column("redirect_url", sa.String(length=255), nullable=True),
        sa.Column("payment_type", sa.String(length=30), nullable=True),
        sa.Column("transaction_status", sa.String(length=20), nullable=True),
        sa.Column("fraud_status", sa.String(length=20), nullable=True),
        sa.Column("transaction_id", sa.String(length=64), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.Column("raw_response", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'paid', 'failed', 'expired', 'refunded')", name="ck_payments_status"),
        sa.UniqueConstraint("midtrans_order_id", name="uq_payments_midtrans_order_id"),
    )
    op.create_index("ix_payments_order_id", "payments", ["order_id"])

    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("order_item_id", sa.Integer(), sa.ForeignKey("order_items.id"), nullable=True),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('earning', 'commission')", name="ck_ledger_entries_kind"),
        sa.UniqueConstraint("order_item_id", "kind", name="uq_ledger_item_kind"),
    )
    op.create_index("ix_ledger_entries_user_id", "ledger_entries", ["user_id"])
    op.create_index("ix_ledger_entries_order_item_id", "ledger_entries", ["order_item_id"])

    # --- backfill data lama: komisi 10%, waktu bayar, dan ledger untuk pesanan yang sudah selesai ---
    op.execute("""
        UPDATE order_items
        SET platform_fee = (price * 1000 + 5000) / 10000,
            seller_net = price - ((price * 1000 + 5000) / 10000)
    """)
    op.execute("UPDATE orders SET paid_at = created_at WHERE status = 'approved'")
    op.execute("""
        INSERT INTO ledger_entries (kind, user_id, order_item_id, amount, available_at, created_at)
        SELECT 'earning', oi.seller_id, oi.id, oi.seller_net, COALESCE(oi.completed_at, now()), now()
        FROM order_items oi JOIN orders o ON o.id = oi.order_id
        WHERE oi.status = 'completed' AND o.status = 'approved'
    """)
    op.execute("""
        INSERT INTO ledger_entries (kind, user_id, order_item_id, amount, available_at, created_at)
        SELECT 'commission', NULL, oi.id, oi.platform_fee, NULL, now()
        FROM order_items oi JOIN orders o ON o.id = oi.order_id
        WHERE oi.status = 'completed' AND o.status = 'approved'
    """)


def downgrade():
    op.drop_index("ix_ledger_entries_order_item_id", table_name="ledger_entries")
    op.drop_index("ix_ledger_entries_user_id", table_name="ledger_entries")
    op.drop_table("ledger_entries")
    op.drop_index("ix_payments_order_id", table_name="payments")
    op.drop_table("payments")
    op.execute("UPDATE order_items SET status = 'in_progress' WHERE status = 'delivered'")
    op.drop_constraint("ck_order_items_status", "order_items", type_="check")
    op.create_check_constraint("ck_order_items_status", "order_items", "status IN ('in_progress', 'completed')")
    for col in ("delivery_url", "delivery_note", "auto_complete_at", "delivered_at", "due_at",
                "seller_net", "platform_fee", "fee_bps"):
        op.drop_column("order_items", col)
    op.drop_column("orders", "paid_at")
