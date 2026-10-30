"""Test data shared by all API tests (the seed of ApiFactory.cs in .NET). Only invented data."""

import re
from datetime import date, datetime, timedelta

import pymysql

from newerp.infrastructure.auth.password_hasher import Pbkdf2PasswordHasher

PASSWORD = "test-password"

ACTIVE_USER_ID, INACTIVE_USER_ID, MANAGER_USER_ID, STOREKEEPER_USER_ID = 1, 2, 3, 4
ADMIN, INACTIVE, MANAGER, STOREKEEPER = "олена", "ігор", "марта", "петро"

# Status and language ids as in the real database.
LANGUAGE_ID = 3
PENDING = 24  # 1) Уточнюємо
COMPLETED = 22  # 5) Відправлено
MANUFACTURING = 25  # 3) Виготовляється
ACCEPTED = 18  # 2) Прийнято
CANCELLED = 19  # Відмінено


ORDER_ID = 1
PRODUCT_ID = 101  # product of ORDER_ID, 2 pieces
SIZE_M_OPTION_VALUE_ID = 47  # ORDER_ID has size M chosen
# An order whose product is linked to a model not directly but through its category and name.
CATEGORY_ORDER_ID = 4
CATEGORY_PRODUCT_ID = 102  # "Сорочка лляна оверсайз", category CATEGORY_ID, size M, 1 piece
CATEGORY_ID = 5
SHIPPING_ORDER_ID = 5  # no waybill yet, cash on delivery
CANCELLED_ORDER_ID = 6  # cancelled order of the customer with RISKY_PHONE
RISKY_PHONE = "+38 (093) 555-44-33"
BRANCH_1 = "Відділення №1: вул. Хрещатик, 1"
BRANCH_1_REF = "wh-kyiv-1"
KYIV_REF = "city-kyiv"
WAITING_TTN = "20450000000002"  # order 2: waits at the branch, paid storage from tomorrow
CASHIER_ID = 1


def insert(connection: pymysql.Connection, table: str, **columns: object) -> None:
    names = ", ".join(f"`{name}`" for name in columns)
    marks = ", ".join(["%s"] * len(columns))
    with connection.cursor() as cursor:
        cursor.execute(f"INSERT INTO `{table}` ({names}) VALUES ({marks})", tuple(columns.values()))


def order(connection: pymysql.Connection, order_id: int, status_id: int, total: int, added: datetime, **extra) -> None:
    """An OpenCart order with every NOT NULL column filled, like the old site writes it."""
    columns: dict[str, object] = {
        name: ""
        for name in (
            "invoice_prefix store_name store_url email fax custom_field comment payment_firstname payment_lastname "
            "payment_company payment_address_1 payment_address_2 payment_city payment_postcode payment_country "
            "payment_zone payment_address_format payment_custom_field payment_method payment_code shipping_firstname "
            "shipping_lastname shipping_company shipping_address_1 shipping_address_2 shipping_city shipping_postcode "
            "shipping_country shipping_zone shipping_address_format shipping_custom_field shipping_method "
            "shipping_code tracking ip forwarded_ip user_agent accept_language"
        ).split()
    }
    columns |= dict.fromkeys(
        ("payment_country_id", "payment_zone_id", "shipping_country_id", "shipping_zone_id", "affiliate_id",
         "commission", "marketing_id", "language_id", "currency_id"),
        0,
    )  # fmt: skip
    columns |= {
        "order_id": order_id,
        "order_status_id": status_id,
        "order_source_id": 1,
        "total": total,
        "date_added": added,
        "date_modified": added,
        "firstname": "Тарас",
        "lastname": f"Клієнт{order_id}",
        "telephone": f"+38067000000{order_id}",
        "currency_code": "UAH",
    }
    insert(connection, "dd_order", **(columns | extra))


def product(connection: pymysql.Connection, product_id: int, model: str, price: int) -> None:
    insert(
        connection, "dd_product", product_id=product_id, model=model, price=price, quantity=5, status=True,
        shipping=True, subtract=True, sku="", upc="", ean="", jan="", isbn="", mpn="", location="",
        stock_status_id=0, manufacturer_id=0, tax_class_id=0, date_available="2026-01-01",
        date_added=datetime(2026, 1, 1), date_modified=datetime(2026, 1, 1),
    )  # fmt: skip


def option_value(connection: pymysql.Connection, value_id: int, option_id: int, product_id: int) -> None:
    insert(
        connection, "dd_product_option_value", product_option_value_id=value_id, product_option_id=option_id,
        product_id=product_id, option_id=11, option_value_id=SIZE_M_OPTION_VALUE_ID, quantity=0, subtract=False,
        price=0, price_prefix="+", points=0, points_prefix="+", weight=0, weight_prefix="+",
    )  # fmt: skip


def order_line(connection, line_id: int, order_id: int, product_id: int, name: str, model: str, quantity: int,
               price: int, option_id: int, value_id: int) -> None:  # fmt: skip
    insert(
        connection, "dd_order_product", order_product_id=line_id, order_id=order_id, product_id=product_id,
        name=name, model=model, quantity=quantity, price=price, total=price * quantity, tax=0, reward=0,
    )  # fmt: skip
    # Size M in the order: the product's option and the customer's choice, as OpenCart writes them.
    insert(
        connection, "dd_order_option", order_id=order_id, order_product_id=line_id, product_option_id=option_id,
        product_option_value_id=value_id, name="Розмір", value="M", type="radio",
    )  # fmt: skip


def run(connection: pymysql.Connection) -> None:
    for status_id, name in (
        (PENDING, "1) Уточнюємо"),
        (COMPLETED, "5) Відправлено"),
        (MANUFACTURING, "3) Виготовляється"),
        (ACCEPTED, "2) Прийнято"),
        (CANCELLED, "Відмінено"),
    ):
        insert(connection, "dd_order_status", order_status_id=status_id, language_id=LANGUAGE_ID, name=name)
    insert(connection, "dd_order_source", order_source_id=1, language_id=LANGUAGE_ID, name="Instagram")

    password_hash = Pbkdf2PasswordHasher(iterations=1_000).hash(PASSWORD)  # cheap: tests log in a lot
    for user_id, name, active, role in (
        (ACTIVE_USER_ID, "Олена", True, "Admin"),
        (INACTIVE_USER_ID, "Ігор", False, "Manager"),
        (MANAGER_USER_ID, "Марта", True, "Manager"),
        (STOREKEEPER_USER_ID, "Петро", True, "Storekeeper"),
    ):
        insert(
            connection,
            "dd_user",
            user_id=user_id,
            user_group_id=1,
            username=name.lower(),
            password="",
            salt="",
            firstname=name,
            lastname="Тест",
            email="",
            image="",
            code="",
            ip="",
            status=active,
            date_added=datetime.now(),
        )
        insert(
            connection,
            "erp_user_login",
            user_id=user_id,
            password_hash=password_hash,
            role=role,
            security_stamp=f"stamp-{user_id}",
            must_change_password=False,
        )

    order(connection, ORDER_ID, PENDING, 1500, datetime(2026, 10, 1, 12))
    order(connection, 2, COMPLETED, 2000, datetime(2026, 10, 2, 12))
    order(connection, 3, 0, 999, datetime(2026, 10, 3, 12))  # unfinished, must not be shown
    order_line(connection, 1, ORDER_ID, PRODUCT_ID, "Сукня", "T-1", 2, 750, option_id=50, value_id=501)
    option_value(connection, 501, 50, PRODUCT_ID)

    order(connection, CATEGORY_ORDER_ID, PENDING, 900, datetime(2026, 9, 1, 12))
    order_line(connection, 2, CATEGORY_ORDER_ID, CATEGORY_PRODUCT_ID, "Сорочка", "T-2", 1, 900, 51, 502)
    insert(connection, "dd_product_to_category", product_id=CATEGORY_PRODUCT_ID, category_id=CATEGORY_ID)
    insert(
        connection, "dd_product_description", product_id=CATEGORY_PRODUCT_ID, language_id=LANGUAGE_ID,
        name="Сорочка лляна оверсайз", description="", tag="", meta_title="", meta_description="", meta_keyword="",
    )  # fmt: skip
    option_value(connection, 502, 51, CATEGORY_PRODUCT_ID)
    insert(
        connection, "erp_order_manager", order_id=2, user_id=ACTIVE_USER_ID, assigned_by_user_id=ACTIVE_USER_ID,
        assigned_at=datetime.now(),
    )  # fmt: skip

    order(
        connection, SHIPPING_ORDER_ID, PENDING, 1200, datetime(2026, 8, 1, 12),
        shipping_city="Київ", shipping_address_2=BRANCH_1, payment_code="cod",
    )  # fmt: skip

    # Product CATEGORY_PRODUCT_ID in the catalog (price and size M), for creating orders by hand.
    product(connection, CATEGORY_PRODUCT_ID, "T-2", 900)
    insert(connection, "dd_option_description", option_id=11, language_id=LANGUAGE_ID, name="Розмір")
    insert(
        connection, "dd_option_value_description", option_value_id=SIZE_M_OPTION_VALUE_ID, language_id=LANGUAGE_ID,
        option_id=11, name="M",
    )  # fmt: skip
    order(
        connection, CANCELLED_ORDER_ID, CANCELLED, 700, datetime(2025, 3, 1, 12), telephone=RISKY_PHONE,
        shipping_method="Нова Пошта", shipping_code="novaposhta_warehouse.novaposhta_warehouse",
        payment_method="Накладений платіж", payment_code="cod",
    )  # fmt: skip

    insert(connection, "dd_stock_categories", category_id=1, name="Тканини", sort_order=0)

    # Order 2's waybill waits at the branch; paid storage starts tomorrow, so it is in the reminders.
    insert(
        connection, "dd_novaposhta_document", order_id=2, carrier="novaposhta", ref=WAITING_TTN, number=WAITING_TTN,
        cost=70, status="Прибув у відділення", status_code=7, recipient_address="Київ, відділення 1",
        redelivery=False, date_created=datetime.now(), date_payed_keeping=date.today() + timedelta(days=1),
    )  # fmt: skip
    for ref, description, category, city_ref, city, weight, working in (
        (BRANCH_1_REF, BRANCH_1, "Branch", KYIV_REF, "Київ", 30, True),
        ("wh-kyiv-2", "Поштомат №2", "Postomat", KYIV_REF, "Київ", 20, True),
        ("wh-kyiv-3", "Відділення №3", "Branch", KYIV_REF, "Київ", 30, False),
        ("wh-kovel-1", "Відділення №1", "Branch", "city-kovel", "Ковель", 30, True),
    ):
        number = re.search(r"№\s*(\d+)", description)
        insert(
            connection, "dd_novaposhta_warehouse", ref=ref, description=description, category_of_warehouse=category,
            city_ref=city_ref, city_description=city, weight=weight, sort_order=int(number.group(1)) if number else 1,
            status=working,
        )  # fmt: skip

    # The default cashier (no payment code); the Checkbox test stub "fiscalizes" in memory.
    insert(
        connection, "dd_cashbox_user", user_id=CASHIER_ID, name="Олена Каса", description="", payment_code="",
        login="", password="", checkbox_key="", customer_id=0, warehouse_id=0, requisites_id=0, status=True,
        date_added=datetime.now(),
    )  # fmt: skip
