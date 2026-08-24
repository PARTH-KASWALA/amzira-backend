import json
import os
import threading
import uuid

import pytest
from alembic import command
from alembic.config import Config
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.security import hash_password
from app.models.address import Address
from app.models.category import Category
from app.models.checkout_payment_intent import CheckoutPaymentIntent
from app.models.product import Product, ProductVariant
from app.models.user import User
from app.services.checkout_payment_service import reserve_checkout_stock


@pytest.mark.skipif(
    os.getenv("AMZIRA_RUN_POSTGRES_CONCURRENCY") != "1",
    reason="Set AMZIRA_RUN_POSTGRES_CONCURRENCY=1 to run the isolated PostgreSQL race test",
)
def test_only_one_checkout_reserves_the_final_unit():
    original_url = settings.DATABASE_URL
    base_url = make_url(original_url)
    database_name = f"amzira_concurrency_{uuid.uuid4().hex[:8]}"
    admin_url = base_url.set(database="postgres")
    test_url = base_url.set(database=database_name)
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    engine = None

    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        settings.DATABASE_URL = test_url.render_as_string(hide_password=False)
        command.upgrade(Config("alembic.ini"), "head")

        engine = create_engine(test_url, pool_size=25, max_overflow=0)
        SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
        with SessionLocal() as db:
            category = Category(name="Concurrency", slug="concurrency", is_active=True)
            user = User(
                email="concurrency@amzira.test",
                full_name="Concurrency Test",
                password_hash=hash_password("StrongPass1"),
                is_active=True,
            )
            db.add_all([category, user])
            db.flush()
            product = Product(
                category_id=category.id,
                name="Final Unit Product",
                slug="final-unit-product",
                base_price=1000,
                is_active=True,
            )
            address = Address(
                user_id=user.id,
                full_name="Concurrency Test",
                phone="9876543210",
                address_line1="Test Street",
                city="Surat",
                state="Gujarat",
                pincode="395007",
                country="India",
                address_type="home",
                is_default=True,
            )
            db.add_all([product, address])
            db.flush()
            variant = ProductVariant(
                product_id=product.id,
                size="24",
                sku="FINAL-UNIT-24",
                stock_quantity=1,
                is_active=True,
            )
            db.add(variant)
            db.flush()
            intents = []
            for index in range(20):
                intent = CheckoutPaymentIntent(
                    user_id=user.id,
                    address_id=address.id,
                    razorpay_order_id=f"order_concurrency_{index}",
                    amount=1050,
                    subtotal=1000,
                    shipping_amount=0,
                    tax_amount=50,
                    total_amount=1050,
                    cart_snapshot=json.dumps([{"variant_id": variant.id, "quantity": 1}]),
                )
                db.add(intent)
                intents.append(intent)
            db.commit()
            variant_id = variant.id
            intent_ids = [intent.id for intent in intents]

        barrier = threading.Barrier(len(intent_ids))
        results: list[bool] = []
        result_lock = threading.Lock()

        def attempt(intent_id: int) -> None:
            with SessionLocal() as db:
                intent = db.query(CheckoutPaymentIntent).filter(CheckoutPaymentIntent.id == intent_id).one()
                barrier.wait()
                try:
                    reserve_checkout_stock(db, intent)
                    db.commit()
                    succeeded = True
                except HTTPException:
                    db.rollback()
                    succeeded = False
                with result_lock:
                    results.append(succeeded)

        threads = [threading.Thread(target=attempt, args=(intent_id,)) for intent_id in intent_ids]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

        assert not any(thread.is_alive() for thread in threads)
        assert results.count(True) == 1
        with SessionLocal() as db:
            variant = db.query(ProductVariant).filter(ProductVariant.id == variant_id).one()
            assert variant.stock_quantity == 0
            assert db.query(CheckoutPaymentIntent).filter(CheckoutPaymentIntent.stock_reserved == True).count() == 1
    finally:
        settings.DATABASE_URL = original_url
        if engine is not None:
            engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(
                text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :name"),
                {"name": database_name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
        admin_engine.dispose()

