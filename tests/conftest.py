"""Test harness (like ApiFactory + TestDatabase in .NET).

Each test module gets its own throwaway MySQL database built from db/*.sql and seeded by tests/seed.py, and runs
the real app against it through TestClient (in memory, no network). The server comes from NEWERP_TEST_DB, a
SQLAlchemy URL without a database name; by default the mysql-test service of docker-compose. Without a server
the tests are skipped.
"""

import os
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

# Settings are read once, so the test values must be in place before the app is imported.
os.environ["NEWERP_ENVIRONMENT"] = "Testing"
os.environ["NEWERP_AUTH_SECRET"] = "test-only-cookie-key"
os.environ["NEWERP_BONUS__PERCENT"] = "10"
os.environ["NEWERP_BONUS__COUNTED_STATUS_IDS"] = "[22]"

import pymysql
import pytest
from anyio.from_thread import start_blocking_portal
from fastapi.testclient import TestClient
from pymysql.constants import CLIENT
from sqlalchemy import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from newerp.infrastructure.db import get_session
from newerp.main import app
from tests import seed

SERVER_URL = make_url(os.environ.get("NEWERP_TEST_DB", "mysql+aiomysql://root:test_root_password@127.0.0.1:3308"))
SCHEMA_FILES = ("schema.sql", "reconstructed.sql", "erp.sql")
DB_DIR = Path(__file__).resolve().parent.parent / "db"


def _connect(database: str | None = None) -> pymysql.Connection:
    return pymysql.connect(
        host=SERVER_URL.host,
        port=SERVER_URL.port or 3306,
        user=SERVER_URL.username,
        password=SERVER_URL.password or "",
        database=database,
        charset="utf8mb4",
        client_flag=CLIENT.MULTI_STATEMENTS,
        autocommit=True,
    )


def _execute_script(connection: pymysql.Connection, sql: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(sql)
        while cursor.nextset():  # read every result of the multi-statement script
            pass


@pytest.fixture(scope="module")
def database() -> Iterator[str]:
    try:
        server = _connect()
    except pymysql.err.OperationalError as error:
        pytest.skip(f"No test MySQL server ({error.args[-1]}); start docker compose or set NEWERP_TEST_DB.")

    name = f"newerp_test_{uuid.uuid4().hex}"[:30]
    _execute_script(server, f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4")
    try:
        with _connect(name) as connection:
            for file in SCHEMA_FILES:
                _execute_script(connection, (DB_DIR / file).read_text(encoding="utf-8"))
            seed.run(connection)
        yield name
    finally:
        _execute_script(server, f"DROP DATABASE IF EXISTS `{name}`")
        server.close()


@pytest.fixture(scope="module")
def db(database: str) -> Iterator[pymysql.Connection]:
    """Direct connection to the test database, to arrange data or check what the API wrote."""
    with _connect(database) as connection:
        yield connection


@pytest.fixture(scope="module")
def api(database: str) -> Iterator[Callable[..., TestClient]]:
    """api() is an anonymous client; api("марта") is a client logged in as that user."""
    engine = create_async_engine(SERVER_URL.set(database=database, query={"charset": "utf8mb4"}))
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def test_session():
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = test_session

    # By default TestClient runs every request on a new event loop. All clients of the module share one loop
    # instead, so the engine can keep its connections open (connecting through Docker is slow).
    with start_blocking_portal() as portal:

        def client(username: str | None = None, password: str = seed.PASSWORD) -> TestClient:
            test_client = TestClient(app)
            test_client.portal = portal  # TestClient uses this portal instead of starting its own
            if username is not None:
                response = test_client.post("/auth/login", json={"username": username, "password": password})
                assert response.status_code == 200, response.text
            return test_client

        yield client
        portal.call(engine.dispose)
    app.dependency_overrides.pop(get_session, None)
