"""Test data shared by all API tests (the seed of ApiFactory.cs in .NET). Only invented data."""

from datetime import datetime

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


def insert(connection: pymysql.Connection, table: str, **columns: object) -> None:
    names = ", ".join(f"`{name}`" for name in columns)
    marks = ", ".join(["%s"] * len(columns))
    with connection.cursor() as cursor:
        cursor.execute(f"INSERT INTO `{table}` ({names}) VALUES ({marks})", tuple(columns.values()))


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
