"""Fills the local database newerp_dev with invented data for development (port of .NET tools/NewErp.DevSeed).

There is no real customer data here and there must never be: Faker invents every name, phone and address.

Run: `uv run python tools/devseed.py` (the same NEWERP_DB_* settings as the API, e.g. the MySQL from docker compose).
Base rows (users, catalog, orders, waybills, customers) go in with SQL. Stock, production, the cashbox, the partner and
locations go through the API itself, in process like a test, so they pass the same checks and handlers as real work
(production operations write off consumables and update the storefront, as in .NET).
"""

import os
import random
from datetime import date, datetime, time, timedelta
from typing import Any
from uuid import uuid4

import pymysql
from faker import Faker
from fastapi.testclient import TestClient

from newerp.infrastructure.auth.password_hasher import Pbkdf2PasswordHasher

DEV_DATABASE = "newerp_dev"
# The seeder writes invented orders, so it works only with the local development database ("mysql": docker compose).
LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "mysql"})
DEV_PASSWORD = "dev"  # every demo user's password
SEED = 2026  # the same seed gives the same data on every run

LANGUAGE_ID = 3  # as in the real database: every lookup has language_id 3
FIRST_STATUS_ID = 24  # "1) Уточнюємо": an order starts with it

# Statuses of the real site (ids, names and colors from dd_order_status).
STATUSES = (
    (24, "1) Уточнюємо", "#48f9f9"), (18, "2) Прийнято", "#ffff00"), (25, "3) Виготовляється", "#ff9900"),
    (21, "4) Чекає на відправку", "#e8d030"), (22, "5) Відправлено", "#00ff00"), (20, "Виконано (B2B)", "#00ff00"),
    (23, "Терміново", "#ff1a1a"), (1, "Перевірити оплату", "#ff00ff"), (17, "В обробці", "#ffffff"),
    (19, "Відмінено", "#156d2c"),
)  # fmt: skip
SHIPPED_STATUS_IDS = frozenset({20, 21, 22})
SENT_STATUS_ID = 22
SOURCES = ("Сайт", "Instagram", "Телефон", "Партнер")

# Product id = FIRST_PRODUCT_ID + position, so the BOMs link to the same products as the orders.
FIRST_PRODUCT_ID = 101
PRODUCTS = (
    "Сукня лляна", "Сорочка оверсайз", "Штани палаццо", "Костюм трикотажний",
    "Спідниця міді", "Жакет класичний", "Футболка базова", "Кардиган в'язаний",
)  # fmt: skip
SIZE_OPTION_ID = 11
SIZE_VALUES = {"S": 46, "M": 47, "L": 48, "XL": 49}  # invented dd_option_value ids, shared by all products
DRESSES_CATEGORY, TOPS_CATEGORY = 20, 21

CITIES = ("Київ", "Львів", "Харків", "Одеса", "Дніпро", "Конотоп", "Ковель", "Житомир")
SHIPPING_METHODS = ("Нова Пошта: відділення", "Нова Пошта: поштомат", "Укрпошта", "Самовивіз (Київ)")
CASH_ON_DELIVERY = "Накладений платіж"
PAYMENT_METHODS = (CASH_ON_DELIVERY, "LiqPay", "Monobank", "Переказ на рахунок")

# The Nova Poshta tracking codes and texts met most often.
TRACKING_STATUSES = (
    (1, "Відправник самостійно створив цю накладну, але ще не надав до відправки"),
    (41, "Відправлення у дорозі"),
    (7, "Прибув на відділення"),
    (8, "Прибув у поштомат"),
    (9, "Відправлення отримано"),
    (11, "Відправлення отримано. Грошовий переказ видано одержувачу."),
    (102, "Відмова від отримання"),
)
WAITING_AT_BRANCH_CODES = frozenset({7, 8})


class SeedRefused(Exception):
    """The target is not an empty local development database; nothing was changed."""


def check_target(host: str, database: str) -> None:
    if host.lower() not in LOCAL_HOSTS or database.lower() != DEV_DATABASE:
        raise SeedRefused(
            f"The seeder works only with {DEV_DATABASE} on localhost, but the settings point to {database} on {host}."
        )


def _empty(data_type: str) -> object:
    """The value for a NOT NULL column without a default, by its MySQL type."""
    if data_type in ("tinyint", "smallint", "mediumint", "int", "bigint", "decimal", "float", "double"):
        return 0
    if data_type in ("datetime", "timestamp"):
        return datetime(2000, 1, 1)
    if data_type == "date":
        return date(2000, 1, 1)
    return ""


class Rows:
    """
    Writes rows with plain SQL. OpenCart has many NOT NULL columns without defaults that NewErp does not use; they
    get empty values read from information_schema, instead of a list by hand (FillOpenCartRequiredFields in .NET).
    """

    def __init__(self, connection: pymysql.Connection) -> None:
        self._connection = connection
        self._required: dict[str, dict[str, object]] = {}

    def add(self, table: str, **columns: object) -> int:
        """Inserts one row and returns its AUTO_INCREMENT id (0 for tables without one)."""
        row = self._defaults(table) | columns
        names = ", ".join(f"`{name}`" for name in row)
        marks = ", ".join(["%s"] * len(row))
        with self._connection.cursor() as cursor:
            cursor.execute(f"INSERT INTO `{table}` ({names}) VALUES ({marks})", tuple(row.values()))
            return cursor.lastrowid

    def update(self, table: str, key: str, key_value: object, **columns: object) -> None:
        assignments = ", ".join(f"`{name}` = %s" for name in columns)
        with self._connection.cursor() as cursor:
            cursor.execute(f"UPDATE `{table}` SET {assignments} WHERE `{key}` = %s", (*columns.values(), key_value))

    def count(self, table: str) -> int:
        with self._connection.cursor() as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM `{table}`")
            return cursor.fetchone()[0]

    def _defaults(self, table: str) -> dict[str, object]:
        if table not in self._required:
            with self._connection.cursor() as cursor:
                cursor.execute(
                    "SELECT column_name, data_type FROM information_schema.columns"
                    " WHERE table_schema = DATABASE() AND table_name = %s AND is_nullable = 'NO'"
                    " AND column_default IS NULL AND extra NOT LIKE %s",
                    (table, "%auto_increment%"),
                )
                self._required[table] = {name: _empty(data_type) for name, data_type in cursor.fetchall()}
        return self._required[table]


class Api:
    """The in-process API client; any error stops the seeder with the response text."""

    def __init__(self, client: TestClient) -> None:
        self._client = client

    def login(self, username: str) -> None:
        self.call("POST", "/auth/login", {"username": username, "password": DEV_PASSWORD})

    def call(self, method: str, path: str, body: object = None) -> Any:
        response = self._client.request(method, path, json=body)
        if response.status_code >= 400:
            raise RuntimeError(f"{method} {path}: {response.status_code} {response.text}")
        return response.json() if response.content else None

    def create(self, path: str, body: object) -> int:
        return self.call("POST", path, body)["id"]


def seed(connection: pymysql.Connection, client: TestClient, order_count: int = 400, today: date | None = None) -> str:
    """Seeds an empty database (raises SeedRefused if it already has orders) and returns what it wrote."""
    rows = Rows(connection)
    if rows.count("dd_order"):
        raise SeedRefused("The database already has orders. The seeder works only with an empty one; nothing changed.")
    return _Seeder(rows, Api(client), today or date.today()).run(order_count)


class _Seeder:
    def __init__(self, rows: Rows, api: Api, today: date) -> None:
        self.rows = rows
        self.api = api
        self.today = today
        self.now = datetime.combine(today, time())
        self.random = random.Random(SEED)
        self.fake = Faker("uk_UA")
        self.fake.seed_instance(SEED)
        self.orders: list[dict[str, Any]] = []
        self.option_values: dict[tuple[int, str], int] = {}  # (product id, size) → product_option_value_id

    def run(self, order_count: int) -> str:
        for status_id, name, color in STATUSES:
            self.rows.add("dd_order_status", order_status_id=status_id, language_id=LANGUAGE_ID, name=name, color=color)
        for source_id, name in enumerate(SOURCES, start=1):
            self.rows.add("dd_order_source", order_source_id=source_id, language_id=LANGUAGE_ID, name=name)
        managers = self.users()
        self.api.login("admin")

        self.catalog()
        branches = self.nova_poshta_branches()
        lines = self.seed_orders(order_count, managers, branches)
        shipments = self.shipments()
        payments = self.finance()
        stock = self.stock()
        self.partner()
        self.product_sort()
        birthdays = self.marketing()
        customers = self.customers()
        returns = self.returns()
        locations = self.locations()
        models = self.production(stock)
        warnings = self.production_operations(models)
        return (
            f"{len(managers) + 3} users, {len(self.orders)} orders, {lines} order lines, {shipments} waybills, "
            f"{payments} bank payments, {len(branches)} Nova Poshta branches, {len(stock)} stock items, 1 partner with "
            f"a feed, 2 product labels, 2 sizes on pre-order, {birthdays} customers with a birthday (some today and "
            f"this week), {customers} registered customers, {returns} returns, {locations} locations, {len(models)} "
            f"production models (cutting and sewing: {warnings} shortage warnings)."
        )

    # ── Users ──

    def users(self) -> list[int]:
        """manager1…manager4 (manager5 is disabled, so there is one to check), admin and sklad; one password."""
        password_hash = Pbkdf2PasswordHasher().hash(DEV_PASSWORD)  # the salt is inside the hash, so one fits all
        managers: list[int] = []
        for username, role, active in (
            *((f"manager{n}", "Manager", n != 5) for n in range(1, 6)),
            ("admin", "Admin", True),
            ("sklad", "Storekeeper", True),
        ):
            user_id = self.rows.add(
                "dd_user", user_group_id=10, username=username, firstname=self.fake.first_name(),
                lastname=self.fake.last_name(), email=f"{username}@example.com", password="", salt="", image="",
                code="", ip="127.0.0.1", status=active,
                date_added=self.fake.date_time_between(self.now - timedelta(days=730), self.now),
            )  # fmt: skip
            self.rows.add(
                "erp_user_login", user_id=user_id, password_hash=password_hash, role=role,
                security_stamp=uuid4().hex, must_change_password=False,
            )  # fmt: skip
            if role == "Manager" and active:
                managers.append(user_id)
        return managers

    # ── Catalog: products, the "Розмір" option, categories ──

    def catalog(self) -> None:
        self.rows.add("dd_option_description", option_id=SIZE_OPTION_ID, language_id=LANGUAGE_ID, name="Розмір")
        for size, value_id in SIZE_VALUES.items():
            self.rows.add(
                "dd_option_value_description", option_value_id=value_id, language_id=LANGUAGE_ID,
                option_id=SIZE_OPTION_ID, name=size,
            )  # fmt: skip
        # Production period of a category (days from the order date): the ready date in the sewing plan.
        for category_id, name, days in (
            (DRESSES_CATEGORY, "Сукні та спідниці", 7),
            (TOPS_CATEGORY, "Сорочки й жакети", 10),
        ):
            self.rows.add(
                "dd_category", category_id=category_id, status=True, production_period=days,
                date_added=self.now, date_modified=self.now,
            )  # fmt: skip
            self.rows.add(
                "dd_category_description", category_id=category_id, language_id=LANGUAGE_ID, name=name,
                description="", meta_title=name, meta_description="", meta_keyword="",
            )  # fmt: skip

        for index, name in enumerate(PRODUCTS):
            product_id = FIRST_PRODUCT_ID + index
            price = 900 + index * 150
            self.rows.add(
                "dd_product", product_id=product_id, model=f"VD-{product_id}", price=price, quantity=10, status=True,
                shipping=True, subtract=True, image=f"catalog/products/vd-{product_id}.jpg",
                date_available=self.today, date_added=self.now, date_modified=self.now,
            )  # fmt: skip
            self.rows.add(
                "dd_product_description", product_id=product_id, language_id=LANGUAGE_ID, name=name, description="",
                tag="", meta_title=name, meta_description="", meta_keyword="",
            )  # fmt: skip
            category = DRESSES_CATEGORY if name.startswith(("Сукня", "Спідниця")) else TOPS_CATEGORY
            self.rows.add("dd_product_to_category", product_id=product_id, category_id=category)
            for size, value_id in SIZE_VALUES.items():
                # As on the site: the size price is the full product price.
                self.option_values[product_id, size] = self.rows.add(
                    "dd_product_option_value", product_option_id=1000 + index, product_id=product_id,
                    option_id=SIZE_OPTION_ID, option_value_id=value_id, quantity=3, subtract=True, price=price,
                    price_prefix="+", points_prefix="+", weight_prefix="+",
                )  # fmt: skip

        # Pre-order: the largest size of the last two products is sold out but sold ahead until a date.
        largest = list(SIZE_VALUES)[-1]
        for index in (len(PRODUCTS) - 2, len(PRODUCTS) - 1):
            self.rows.update(
                "dd_product_option_value", "product_option_value_id",
                self.option_values[FIRST_PRODUCT_ID + index, largest],
                quantity=-2 if index == len(PRODUCTS) - 1 else 0, pre_order_quantity=5,
                date_available=self.today + timedelta(days=14),
            )  # fmt: skip

    def nova_poshta_branches(self) -> list[dict[str, Any]]:
        """In every city six branches, two parcel lockers and one closed branch (hidden in hints). Invented refs."""
        branches: list[dict[str, Any]] = []
        for i, city in enumerate(CITIES):
            entries = [
                (f"dev-wh-{i}-{n}", f"Відділення №{n}: вул. Тестова, {n}", "Branch", 30, True, n) for n in range(1, 7)
            ]
            entries += [
                (f"dev-wh-{i}-p1", f'Поштомат "Нова Пошта" №{100 + i}', "Postomat", 20, True, 100 + i),
                (f"dev-wh-{i}-p2", f'Поштомат "Нова Пошта" №{200 + i}', "Postomat", 20, True, 200 + i),
                (f"dev-wh-{i}-x", "Відділення №99: зачинено", "Branch", 30, False, 99),
            ]
            for ref, description, category, weight, working, number in entries:
                branch = {
                    "ref": ref, "description": description, "category_of_warehouse": category,
                    "city_ref": f"dev-city-{i}", "city_description": city, "weight": weight, "sort_order": number,
                    "status": working,
                }  # fmt: skip
                self.rows.add("dd_novaposhta_warehouse", **branch)
                branches.append(branch)
        return branches

    # ── Orders ──

    def seed_orders(self, count: int, managers: list[int], branches: list[dict[str, Any]]) -> int:
        lines = 0
        sizes = list(SIZE_VALUES)
        for number in range(1, count + 1):
            firstname, lastname = self.fake.first_name(), self.fake.last_name()
            user = self.fake.user_name()
            city = self.random.choice(CITIES)
            shipping = self.random.choice(SHIPPING_METHODS)
            payment = self.random.choice(PAYMENT_METHODS)
            # As on the old site: the Nova Poshta branch goes to address_2, cash on delivery is payment_code "cod".
            city_branches = [b["description"] for b in branches if b["city_description"] == city]
            branch = self.random.choice(city_branches) if shipping.startswith("Нова Пошта") else ""
            added = self.fake.date_time_between(self.now - timedelta(days=180), self.now)
            status_id = self.random.choice(STATUSES)[0]
            order_lines = []
            for _ in range(self.random.randint(1, 3)):
                product = self.random.randrange(len(PRODUCTS))
                order_lines.append((product, self.random.randint(8, 44) * 100, self.random.randint(1, 2)))
            order = {
                "firstname": firstname, "lastname": lastname,
                "email": f"{user if user.isascii() else 'client'}{number}@example.com",
                "telephone": f"+38067{self.random.randint(0, 9_999_999):07d}", "shipping_city": city,
                "shipping_address_2": branch, "shipping_method": shipping, "payment_method": payment,
                "payment_code": "cod" if payment == CASH_ON_DELIVERY else "", "order_status_id": status_id,
                "order_source_id": self.random.randint(1, len(SOURCES)), "date_added": added,
                "total": sum(price * quantity for _, price, quantity in order_lines),
            }  # fmt: skip
            comment = self.fake.sentence() if self.random.random() < 0.2 else ""
            order["order_id"] = self.rows.add(
                "dd_order", **order, comment=comment, date_modified=added, payment_firstname=firstname,
                payment_lastname=lastname, shipping_firstname=firstname, shipping_lastname=lastname,
                payment_city=city, payment_country="Україна", shipping_country="Україна", payment_country_id=220,
                shipping_country_id=220, currency_code="UAH", currency_id=1, currency_value=1, language_id=LANGUAGE_ID,
                store_name="Demo Store (dev)", store_url="http://localhost/", invoice_prefix="DEV-", ip="127.0.0.1",
            )  # fmt: skip
            order["lines"] = []
            for product, price, quantity in order_lines:
                product_id = FIRST_PRODUCT_ID + product
                name, model = PRODUCTS[product], f"VD-{self.random.randint(1000, 9999)}"
                line_id = self.rows.add(
                    "dd_order_product", order_id=order["order_id"], product_id=product_id, name=name, model=model,
                    quantity=quantity, price=price, total=price * quantity, tax=0, reward=0,
                )  # fmt: skip
                # The chosen size, as OpenCart writes it when the order is placed.
                size = self.random.choice(sizes)
                self.rows.add(
                    "dd_order_option", order_id=order["order_id"], order_product_id=line_id,
                    product_option_id=1000 + product, product_option_value_id=self.option_values[product_id, size],
                    name="Розмір", value=size, type="radio",
                )  # fmt: skip
                order["lines"].append({"product_id": product_id, "name": name, "model": model})
                lines += 1

            if self.random.random() < 0.75:
                manager = self.random.choice(managers)
                self.rows.add(
                    "erp_order_manager", order_id=order["order_id"], user_id=manager, assigned_by_user_id=manager,
                    assigned_at=added + timedelta(minutes=15),
                )  # fmt: skip
                if status_id != FIRST_STATUS_ID:
                    self.rows.add(
                        "dd_order_status_log", order_id=order["order_id"], user_id=manager,
                        old_status_id=FIRST_STATUS_ID, new_status_id=status_id,
                        date_added=added + timedelta(hours=self.random.randint(1, 47)),
                    )  # fmt: skip
            order["customer_id"] = 0
            self.orders.append(order)
        return lines

    def shipments(self) -> int:
        """
        Invented waybills for shipped orders: number 2045 + the order id, different tracking statuses. Parcels at a
        branch get paid storage in 0–4 days from today, so the reminder list is not empty.
        """
        waiting = 0
        shipped = [o for o in self.orders if o["order_status_id"] in SHIPPED_STATUS_IDS]
        for order in shipped:
            number = f"2045{order['order_id']:010d}"
            code, status = self.random.choice(TRACKING_STATUSES)
            keeping = None
            if code in WAITING_AT_BRANCH_CODES:
                keeping = self.today + timedelta(days=waiting % 5)
                waiting += 1
            self.rows.add(
                "dd_novaposhta_document", order_id=order["order_id"], carrier="novaposhta", ref=number, number=number,
                cost=self.random.randint(60, 150), status=status, status_code=code,
                recipient_address=f"{order['shipping_city']}, {order['shipping_address_2']}",
                redelivery=order["payment_method"] == CASH_ON_DELIVERY,
                date_created=order["date_added"] + timedelta(days=1),
                date_scheduled=order["date_added"].date() + timedelta(days=3), date_payed_keeping=keeping,
            )  # fmt: skip
        return len(shipped)

    def finance(self) -> int:
        """
        The Checkbox cashbox (warehouse, requisites, two cashiers) and an invented bank statement: half of the
        payments are already matched to orders, the rest wait for a manager. A few cash-on-delivery orders get a
        prepayment.
        """
        warehouse = self.api.create(
            "/cashbox/warehouses", {"name": "Склад Київ", "description": "Вигаданий склад для розробки"}
        )
        requisites = self.api.create(
            "/cashbox/requisites", {"name": "ФОП Тестовий", "description": "Вигадані реквізити для розробки"}
        )
        for name, description, code, close in (("Каса сайту", "Типовий касир", "", None),
                                                ("Каса Nova Pay", "Накладений платіж", "cod", "23:00:00")):  # fmt: skip
            self.api.create(
                "/cashbox/cashiers",
                {"name": name, "description": description, "paymentCode": code, "autoCloseTime": close,
                 "warehouseId": warehouse, "requisitesId": requisites, "isActive": True},
            )  # fmt: skip

        for order in [o for o in self.orders if o["payment_code"] == "cod"][:5]:
            self.rows.update("dd_order", "order_id", order["order_id"], prepayment=200)

        for count, order in enumerate(self.orders[:8]):
            amount = f"{order['total']:.2f} UAH"
            self.rows.add(
                "dd_privatbank", appcode=f"dev-{order['order_id']}", card="26000000000000", amount=amount,
                cardamount=amount, rest="", description=f"Оплата за замовлення {order['order_id']}",
                terminal=f"{order['firstname']} {order['lastname']}", trandate=order["date_added"].date(),
                trantime=time(10 + count, 15), status="", show_status=True,
                order_id=order["order_id"] if count % 2 == 0 else 0,
            )  # fmt: skip
        return min(8, len(self.orders))

    # ── Stock and production (through the API) ──

    def stock(self) -> dict[str, int]:
        """Consumables with an opening balance. Prices are invented, only so the reorder report has sums."""
        categories = {
            name: self.api.create("/stock/categories", {"name": name}) for name in ("Тканини", "Фурнітура", "Пакування")
        }
        fabrics = self.api.create(
            "/stock/suppliers",
            {"name": 'ТОВ "Текстиль-Опт" (вигаданий)', "phone": "+380441234567", "categories": "Тканини"},
        )
        fittings = self.api.create(
            "/stock/suppliers",
            {"name": "ФОП Фурнітура (вигаданий)", "categories": "Фурнітура", "notes": "Доставка по вівторках"},
        )
        items: dict[str, int] = {}
        for category, supplier, name, unit, minimum, start, price in (
            ("Тканини", fabrics, "Льон бежевий", "м", 30, 85.5, 240),
            ("Тканини", fabrics, "Трикотаж чорний", "м", 40, 22, 180),
            ("Тканини", fabrics, "Віскоза принт", "м", 20, 64, 210),
            ("Фурнітура", fittings, "Ґудзики дерев'яні 15 мм", "шт", 200, 1350, 3.5),
            ("Фурнітура", fittings, "Блискавка прихована 20 см", "шт", 100, 45, 12),
            ("Пакування", None, "Пакет кур'єрський 30x40", "шт", 300, 520, 2.2),
            ("Пакування", None, "Бірка з логотипом", "шт", 500, 180, 1.8),
        ):
            items[name] = self.api.create(
                "/stock/items",
                {"categoryId": categories[category], "supplierId": supplier, "name": name, "unit": unit,
                 "minQuantity": minimum, "price": price},
            )  # fmt: skip
            self.api.call(
                "POST", f"/stock/items/{items[name]}/adjustments", {"delta": start, "reason": "Початковий залишок"}
            )
        return items

    def production(self, item: dict[str, int]) -> list[dict[str, Any]]:
        """
        Production models for the first products: sizes linked to the "Розмір" option, cut parts, cutting and sewing
        BOMs, and one ready-made model. Stock starts at zero, so every model is in the "to sew" list at first.
        """
        bag, tag = item["Пакет кур'єрський 30x40"], item["Бірка з логотипом"]
        # (product, fabric, metres for the front, metres for the back, (fitting, per piece))
        specs = (
            (0, item["Льон бежевий"], 1.3, 1.5, (item["Блискавка прихована 20 см"], 1)),  # Сукня лляна
            (1, item["Віскоза принт"], 1.0, 1.2, (item["Ґудзики дерев'яні 15 мм"], 8)),  # Сорочка оверсайз
            (3, item["Трикотаж чорний"], 1.4, 1.7, (item["Блискавка прихована 20 см"], 1)),  # Костюм трикотажний
        )
        sizes = [{"name": name, "percent": percent} for name, percent in (("S", 25), ("M", 40), ("L", 25), ("XL", 10))]
        models = []
        for product, fabric, front_metres, back_metres, (fitting, fitting_qty) in specs:
            model_id = self.api.create(
                "/production/models",
                {"name": PRODUCTS[product], "type": "clothing", "minTotal": 30, "sortOrder": product, "sizes": sizes},
            )
            model = self.api.call("GET", f"/production/models/{model_id}")
            size_ids = {size["name"]: size["id"] for size in model["sizes"]}
            # The model's sizes link to the same "Розмір" values as the orders.
            self.api.call(
                "PUT", f"/production/models/{model_id}/links",
                {"productIds": [FIRST_PRODUCT_ID + product], "categories": [],
                 "sizeOptions": [{"sizeId": size_ids[name], "optionValueId": SIZE_VALUES[name]} for name in size_ids]},
            )  # fmt: skip
            front = self.api.create(f"/production/models/{model_id}/components", {"name": "Перед", "sortOrder": 1})
            back = self.api.create(f"/production/models/{model_id}/components", {"name": "Спинка", "sortOrder": 2})
            front_rows = [
                {"sizeId": None, "itemId": fabric, "qtyPerUnit": front_metres},
                {"sizeId": size_ids["XL"], "itemId": fabric, "qtyPerUnit": round(front_metres + 0.2, 1)},  # more for XL
            ]
            self.api.call("PUT", f"/production/components/{front}/bom", {"rows": front_rows})
            self.api.call(
                "PUT", f"/production/components/{back}/bom",
                {"rows": [{"sizeId": None, "itemId": fabric, "qtyPerUnit": back_metres}]},
            )  # fmt: skip
            # For one sewn piece: one of each cut part, the fitting, a tag and a bag.
            self.api.call(
                "PUT", f"/production/models/{model_id}/sewn-bom",
                {"rows": [{"componentId": front, "qtyPerUnit": 1}, {"componentId": back, "qtyPerUnit": 1},
                          {"itemId": fitting, "qtyPerUnit": fitting_qty}, {"itemId": tag, "qtyPerUnit": 1},
                          {"itemId": bag, "qtyPerUnit": 1}]},
            )  # fmt: skip
            models.append({"id": model_id, "type": "clothing", "size_ids": list(size_ids.values())})

        bag_model = self.api.create(
            "/production/models",
            {"name": "Шопер (готовий, від постачальника)", "type": "ready_made", "minTotal": 10, "sortOrder": 99,
             "sizes": [{"name": "Єдиний", "percent": 100}]},
        )  # fmt: skip
        sizes_of_bag = [size["id"] for size in self.api.call("GET", f"/production/models/{bag_model}")["sizes"]]
        models.append({"id": bag_model, "type": "ready_made", "size_ids": sizes_of_bag})
        return models

    def production_operations(self, models: list[dict[str, Any]]) -> int:
        """Cutting and sewing through the real endpoints. The knit is short on purpose, so the suit gets warnings."""
        warnings = 0

        def record(model: dict[str, Any], operation: str, per_size: int) -> list[str]:
            body = {
                "quantities": [{"sizeId": s, "quantity": per_size} for s in model["size_ids"]],
                "comment": "Початкові дані",
            }
            return self.api.call("POST", f"/production/models/{model['id']}/{operation}", body)["warnings"]

        for model in models:
            if model["type"] == "clothing":
                warnings += len(record(model, "cut", 4))
                record(model, "to-sewing", 3)
                warnings += len(record(model, "sewn", 3))
            else:
                record(model, "sewn", 15)  # ready-made goods: a delivery from the supplier
        return warnings

    # ── Partner, product sort, marketing, customers, returns, locations ──

    def partner(self) -> None:
        """Partner "eva" with its feed on (/feeds/eva.xml): dresses with a markup, and a shirt with its own price."""
        self.api.create(
            "/partners",
            {"code": "eva", "name": "EVA (вигаданий партнер)", "isActive": True, "feedUrl": "", "feedEnabled": True,
             "vendorName": "Vidro", "notes": "", "defaultQuantity": 5,
             "categoryMap": [
                 {"categoryId": DRESSES_CATEGORY, "externalId": "1001", "externalName": "Жіночий одяг / Сукні",
                  "percentSupply": 15, "percentSale": 30},
                 {"categoryId": DRESSES_CATEGORY, "sizeName": "XL", "externalId": "1002",
                  "externalName": "Жіночий одяг / Великі розміри", "percentSupply": 20},
             ],
             "productMap": [{"productId": FIRST_PRODUCT_ID + 1, "externalId": "EVA-SHIRT-01", "externalName": "",
                             "priceSupply": 1200, "priceSale": 1500}]},
        )  # fmt: skip

    def product_sort(self) -> None:
        """For product sort: labels "Хіт" and "Новинка", every product in store 0, the shirts category in cross-sell."""
        for label_id, name, color in ((1, "Хіт", "#d32f2f"), (2, "Новинка", "#388e3c")):
            self.rows.add(
                "dd_product_label", label_id=label_id, name=name, background_color=color, text_color="#ffffff",
                sort_order=label_id, status=True,
            )  # fmt: skip
        for index in range(len(PRODUCTS)):
            product_id = FIRST_PRODUCT_ID + index
            self.rows.add("dd_product_to_store", product_id=product_id, store_id=0)
            if index < 3:
                self.rows.add("dd_product_to_label", product_id=product_id, label_id=1)
            if index >= len(PRODUCTS) - 2:
                self.rows.add("dd_product_to_label", product_id=product_id, label_id=2)
        self.rows.add("dd_crosssell_item", type="category", target_id=TOPS_CATEGORY)

    def marketing(self) -> int:
        """
        Birthdays in some orders (a few today and this week, so the page is not empty), Instagram names, and product
        questions and newsletter subscriptions for the contact export.
        """
        birthdays = 0
        for i in range(0, len(self.orders), 7):
            day = self.today + timedelta(days=i // 7 if i < 7 * 5 else self.random.randrange(365))
            self.rows.update(
                "dd_order", "order_id", self.orders[i]["order_id"], birthday_day=day.day, birthday_month=day.month
            )
            birthdays += 1
        for order in self.orders[::11]:
            instagram = "@" + order["firstname"].lower() + str(order["order_id"])
            self.rows.update("dd_order", "order_id", order["order_id"], instagram=instagram)
        for i in range(20):
            self.rows.add(
                "dd_question", product_id=FIRST_PRODUCT_ID + i % len(PRODUCTS), name=f"Гість {i + 1}",
                telephone=f"+38 (093) {i:03d}-00-{i % 100:02d}", question="Чи буде цей розмір знову?",
                date_added=self.now - timedelta(days=i * 3),
            )  # fmt: skip
            self.rows.add(
                "dd_subscribe", email=f"subscriber{i + 1}@example.com", date_added=self.now - timedelta(days=i * 5)
            )
        return birthdays

    def customers(self) -> int:
        """
        Every fifth customer of the orders "registered" (the order gets customer_id). No password, so nobody can log
        in to the site with them. Some of them also ordered as a guest with the phone written differently.
        """
        for group_id, name in ((1, "Роздріб"), (2, "Опт")):
            self.rows.add(
                "dd_customer_group_description", customer_group_id=group_id, language_id=LANGUAGE_ID, name=name,
                description="",
            )  # fmt: skip
        registered = self.orders[::5]
        for i, order in enumerate(registered):
            position = i * 5  # the order's place in self.orders
            customer_id = self.rows.add(
                "dd_customer", customer_group_id=2 if i % 8 == 0 else 1, language_id=LANGUAGE_ID,
                firstname=order["firstname"], lastname=order["lastname"], email=order["email"],
                telephone=order["telephone"], fax="", password="", salt="", custom_field="", ip="",
                newsletter=i % 3 == 0, status=i % 10 != 9, token="", code="",
                date_added=order["date_added"] - timedelta(minutes=5),
            )  # fmt: skip
            address_id = self.rows.add(
                "dd_address", customer_id=customer_id, firstname=order["firstname"], lastname=order["lastname"],
                company="", address_1=order["shipping_address_2"], address_2="", city=order["shipping_city"],
                postcode="", custom_field="",
            )  # fmt: skip
            self.rows.update("dd_customer", "customer_id", customer_id, address_id=address_id)
            self.rows.update("dd_order", "order_id", order["order_id"], customer_id=customer_id)
            order["customer_id"] = customer_id
            if i % 6 == 0 and position + 1 < len(self.orders):
                # The next order: the same person as a guest, phone written differently; the card finds it by phone.
                guest = self.orders[position + 1]
                digits = "".join(ch for ch in order["telephone"] if ch.isdigit())
                guest.update(firstname=order["firstname"], lastname=order["lastname"], telephone=digits)
                self.rows.update(
                    "dd_order", "order_id", guest["order_id"], firstname=guest["firstname"],
                    lastname=guest["lastname"], telephone=digits,
                )  # fmt: skip
            if i % 4 == 0:
                self.rows.add(
                    "dd_customer_history", customer_id=customer_id, comment="Просила дзвонити після 18:00.",
                    date_added=order["date_added"] + timedelta(days=1),
                )  # fmt: skip
        return len(registered)

    def returns(self) -> int:
        """Return lookups and a few returns from sent orders, as a manager would file them."""
        statuses = ("Очікує", "Отримано", "Завершено")
        lookups = {
            "dd_return_status": ("return_status_id", statuses),
            "dd_return_reason": ("return_reason_id", ("Не підійшов розмір", "Брак", "Не той товар", "Інше")),
            "dd_return_action": ("return_action_id", ("Повернено кошти", "Обмін", "Відмовлено")),
        }
        for table, (key, names) in lookups.items():
            for lookup_id, name in enumerate(names, start=1):
                self.rows.add(table, **{key: lookup_id}, language_id=LANGUAGE_ID, name=name)

        sent = [(o, line) for o in self.orders if o["order_status_id"] == SENT_STATUS_ID for line in o["lines"]][:6]
        for i, (order, line) in enumerate(sent):
            added = order["date_added"] + timedelta(days=10 + i)
            status_id = i % len(statuses) + 1
            return_id = self.rows.add(
                "dd_return", order_id=order["order_id"], product_id=line["product_id"],
                customer_id=order["customer_id"], firstname=order["firstname"], lastname=order["lastname"],
                email=order["email"],
                telephone=order["telephone"], product=line["name"], model=line["model"], quantity=1,
                opened=i % 2 == 0, return_reason_id=i % 4 + 1, return_action_id=1 if status_id == 3 else 0,
                return_status_id=status_id, comment="Клієнт написав у Viber.", date_ordered=order["date_added"].date(),
                date_added=added, date_modified=added + timedelta(days=status_id - 1),
            )  # fmt: skip
            for step in range(1, status_id + 1):
                self.rows.add(
                    "dd_return_history", return_id=return_id, return_status_id=step, notify=False, comment="",
                    date_added=added + timedelta(days=step - 1),
                )  # fmt: skip
        return len(sent)

    def locations(self) -> int:
        """The "Склади й пункти видачі" lookup: a warehouse and two pickup points with invented addresses."""
        items = (
            ("Склад і цех", "склад", "Київ", "вул. Вигадана, 10", "Комірник", "+38 (067) 000-00-01"),
            (
                "Пункт видачі на Подолі",
                "пункт видачі",
                "Київ",
                "вул. Тестова, 5",
                "Адміністратор",
                "+38 (067) 000-00-02",
            ),
            (
                "Пункт видачі у Львові",
                "пункт видачі",
                "Львів",
                "пл. Прикладна, 3",
                "Адміністратор",
                "+38 (067) 000-00-03",
            ),
        )
        for name, kind, city, address, contact, phone in items:
            self.api.create(
                "/locations",
                {"name": name, "type": kind, "city": city, "address": address, "contact": contact,
                 "telephone": phone, "description": ""},
            )  # fmt: skip
        return len(items)


def main() -> int:
    # In-process requests are plain HTTP, and outside Development the auth cookie is HTTPS-only.
    os.environ["NEWERP_ENVIRONMENT"] = "Development"
    from newerp.config import get_settings  # imported after the line above: settings are read once
    from newerp.main import app

    settings = get_settings()
    try:
        check_target(settings.db_host, settings.db_name)
    except SeedRefused as refused:
        print("Refused:", refused)
        return 2
    try:
        connection = pymysql.connect(
            host=settings.db_host, port=settings.db_port, user=settings.db_user,
            password=settings.db_password.get_secret_value(), database=settings.db_name, charset="utf8mb4",
            autocommit=True,
        )  # fmt: skip
        # `with TestClient(app)`: all requests share one event loop, so the app's connection pool keeps working.
        with connection, TestClient(app) as client:
            print("Done:", seed(connection, client))
    except SeedRefused as refused:
        print("Refused:", refused)
        return 1
    print(f"Log in (password {DEV_PASSWORD}): admin (Admin), sklad (Storekeeper), manager1…manager4 (Manager).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
