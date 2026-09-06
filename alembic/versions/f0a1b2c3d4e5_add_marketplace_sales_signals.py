"""add transparent marketplace sales signal provenance

Revision ID: f0a1b2c3d4e5
Revises: e8f9a0b1c2d3
Create Date: 2026-09-06 16:45:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f0a1b2c3d4e5"
down_revision: Union[str, None] = "e8f9a0b1c2d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("products", sa.Column("marketplace_signal_label", sa.String(length=80), nullable=True))
    op.add_column("products", sa.Column("marketplace_signal_source", sa.String(length=120), nullable=True))
    op.add_column("products", sa.Column("marketplace_signal_observed_at", sa.DateTime(), nullable=True))
    op.add_column("products", sa.Column("marketplace_signal_units", sa.Integer(), nullable=True))
    op.add_column("products", sa.Column("marketplace_signal_evidence_ref", sa.String(length=255), nullable=True))
    op.add_column("products", sa.Column("marketplace_signal_match_method", sa.String(length=40), nullable=True))

    # These records use exact seller-SKU to DTC-style-code matches in AMZIRA's
    # own Flipkart seller report. The source reports a 2 PM same-day snapshot,
    # so it is stored as an observed sales signal rather than a timeless
    # bestseller or customer-rating assertion.
    for style_code, label, units in (
        ("KLC-RED-GLD-JAQ-00-0206", "Marketplace top seller", 3),
        ("KLC-EMG-GLD-JAQ-00-0212", "Marketplace pick", 1),
        ("KLC-HPK-NVY-TRD-00-0307", "Marketplace pick", 1),
    ):
        op.execute(
            sa.text(
                """
                UPDATE products
                SET marketplace_signal_label = :label,
                    marketplace_signal_source = :source,
                    marketplace_signal_observed_at = :observed_at,
                    marketplace_signal_units = :units,
                    marketplace_signal_evidence_ref = :evidence_ref,
                    marketplace_signal_match_method = :match_method
                WHERE style_code = :style_code
                """
            ).bindparams(
                label=label,
                source="Flipkart seller performance snapshot",
                observed_at="2026-09-06 14:00:00",
                units=units,
                evidence_ref="top_products_2_00_PM.csv",
                match_method="exact_seller_sku",
                style_code=style_code,
            )
        )


def downgrade() -> None:
    for column_name in (
        "marketplace_signal_match_method",
        "marketplace_signal_evidence_ref",
        "marketplace_signal_units",
        "marketplace_signal_observed_at",
        "marketplace_signal_source",
        "marketplace_signal_label",
    ):
        op.drop_column("products", column_name)
