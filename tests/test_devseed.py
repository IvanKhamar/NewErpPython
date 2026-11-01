"""The demo data seeder (tools/devseed.py): it fills an empty database so every module has data, and nothing else."""

import pytest

from tests.conftest import new_database
from tools.devseed import DEV_PASSWORD, SeedRefused, check_target, seed

ORDERS = 40  # fewer than the real 400, to keep the test fast


@pytest.fixture(scope="module")
def database():
    """Overrides the shared fixture: the same schema, but empty, so the seeder starts from nothing."""
    with new_database(lambda _: None) as name:
        yield name


@pytest.fixture(scope="module")
def summary(api, db) -> str:
    return seed(db, api(), order_count=ORDERS)


def count(db, table: str) -> int:
    with db.cursor() as cursor:
        cursor.execute(f"SELECT COUNT(*) FROM `{table}`")
        return cursor.fetchone()[0]


@pytest.mark.parametrize(
    ("host", "database_name"),
    [("db.example.com", "newerp_dev"), ("localhost", "newerp_prod"), ("127.0.0.1", "newerp_test")],
)
def test_check_target_refuses_anything_but_the_local_dev_database(host, database_name):
    with pytest.raises(SeedRefused):
        check_target(host, database_name)


def test_check_target_accepts_localhost_and_the_compose_host():
    check_target("localhost", "newerp_dev")
    check_target("mysql", "NEWERP_DEV")


def test_seed_fills_every_module(summary, db, api):
    assert f"{ORDERS} orders" in summary
    assert (count(db, "dd_user"), count(db, "erp_user_login"), count(db, "dd_order")) == (7, 7, ORDERS)

    admin = api("admin", DEV_PASSWORD)
    assert admin.get("/orders?pageSize=100").json()["totalCount"] == ORDERS
    assert admin.get("/reports/dashboard").status_code == 200
    assert len(admin.get("/production/models").json()) == 4
    assert len(admin.get("/stock/items").json()) == 7
    assert [p["code"] for p in admin.get("/partners").json()] == ["eva"]
    assert len(admin.get("/locations").json()) == 3
    # The production operations went through the real handlers: sewn pieces exist and consumables were used.
    assert count(db, "dd_stock_production_log") > 0
    assert count(db, "dd_stock_log") > 7

    # manager5 is the disabled one.
    assert api().post("/auth/login", json={"username": "manager5", "password": DEV_PASSWORD}).status_code == 401
    api("manager1", DEV_PASSWORD)


def test_second_run_changes_nothing(summary, db, api):
    with pytest.raises(SeedRefused):
        seed(db, api(), order_count=ORDERS)
    assert count(db, "dd_order") == ORDERS
