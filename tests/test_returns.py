"""Product returns. Its own database: the test adds lookups and returns."""

import asyncio
from datetime import datetime

import pytest

from newerp.application.common import NotFoundError
from newerp.application.returns import (
    AddReturnHistoryRequest,
    CreateReturnRequest,
    ReturnLookupItem,
    ReturnLookupsDto,
    ReturnService,
    ReturnSourceOrder,
)
from newerp.domain.common import DomainError
from newerp.domain.returns import ReturnableLine, validate_return
from tests.seed import LANGUAGE_ID, MANAGER, ORDER_ID, PRODUCT_ID, STOREKEEPER, insert

WAITING, RECEIVED, WRONG_SIZE, REFUNDED = 1, 2, 1, 1

# ═══ Rules ═══

LINES = [
    ReturnableLine(101, "Сукня", "SL-01", 2),
    ReturnableLine(102, "Пояс", "P-01", 1),
    ReturnableLine(101, "Сукня", "SL-01", 1),
]


def check(product_id=101, quantity=1, reason_id=1, already_returned=0, firstname="Тарас", telephone="0671234567",
          email=""):  # fmt: skip
    return validate_return(
        firstname, "", telephone, email, LINES, product_id, quantity, reason_id, None, already_returned
    )


def test_product_from_order_up_to_bought_minus_returned_across_same_product_lines():
    assert check(quantity=3).name == "Сукня"
    for kwargs, message in (
        ({"quantity": 4}, "від 1 до 3"),
        ({"quantity": 2, "already_returned": 2}, "від 1 до 1"),
        ({"product_id": 102, "already_returned": 1}, "повністю"),
        ({"product_id": 999}, "немає в замовленні"),
        ({"quantity": 0}, "від 1 до 3"),
    ):
        with pytest.raises(DomainError, match=message):
            check(**kwargs)


def test_customer_and_reason_checked_like_old_form_but_email_optional():
    assert check(email="").name == "Сукня"
    for kwargs, message in (
        ({"firstname": " "}, "Ім'я"),
        ({"telephone": "12"}, "Телефон"),
        ({"reason_id": 0}, "причину"),
        ({"email": "a" * 97}, "E-mail"),
    ):
        with pytest.raises(DomainError, match=message):
            check(**kwargs)


# ═══ Service ═══


class FakeStore:
    def __init__(self) -> None:
        self.orders = {
            7: ReturnSourceOrder(
                7,
                "Тарас",
                "Шевченко",
                "0671234567",
                "",
                0,
                datetime(2026, 10, 1),
                [ReturnableLine(101, "Сукня", "SL-01", 2)],
                {101: 1},
            )  # fmt: skip
        }
        self.created = []
        self.history = []

    async def lookups(self):
        return ReturnLookupsDto(
            statuses=[ReturnLookupItem(id=1, name="Очікує"), ReturnLookupItem(id=2, name="Отримано")],
            reasons=[ReturnLookupItem(id=1, name="Не підійшов розмір")],
            actions=[ReturnLookupItem(id=1, name="Повернено кошти")],
        )

    async def get_source_order(self, order_id):
        return self.orders.get(order_id)

    async def create(self, record):
        self.created.append(record)
        return 55

    async def add_history(self, return_id, status_id, comment, action_id, now):
        if return_id != 55:
            return False
        self.history.append((return_id, status_id, comment, action_id))
        return True


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 10, 19, 9, 0)


def request(order_id=7, product_id=101, quantity=1, reason_id=1, status_id=1, **extra) -> CreateReturnRequest:
    return CreateReturnRequest(
        order_id=order_id, product_id=product_id, quantity=quantity, reason_id=reason_id, status_id=status_id, **extra
    )


def test_create_takes_customer_and_product_from_order_counts_already_returned():
    store = FakeStore()
    service = ReturnService(store, FixedClock())

    return_id = asyncio.run(service.create(request(comment=" розмір ")))

    [record] = store.created
    assert (return_id, record.line.name, record.order.firstname, record.now) == (
        55, "Сукня", "Тарас", datetime(2026, 10, 19, 9, 0),
    )  # fmt: skip
    with pytest.raises(DomainError, match="від 1 до 1"):
        asyncio.run(service.create(request(quantity=2)))


@pytest.mark.parametrize("bad", [{"order_id": 8}, {"status_id": 9}, {"reason_id": 9}, {"action_id": 9}])
def test_create_unknown_order_status_reason_or_action_fails(bad):
    store = FakeStore()
    with pytest.raises(DomainError):
        asyncio.run(ReturnService(store, FixedClock()).create(request(**bad)))
    assert store.created == []


def test_add_history_checks_status_and_action_trims_comment_not_found_for_missing_return():
    store = FakeStore()
    service = ReturnService(store, FixedClock())

    asyncio.run(service.add_history(55, AddReturnHistoryRequest(status_id=2, comment="  отримали  ", action_id=1)))

    assert store.history == [(55, 2, "отримали", 1)]
    for bad in (AddReturnHistoryRequest(status_id=9), AddReturnHistoryRequest(status_id=2, action_id=9)):
        with pytest.raises(DomainError):
            asyncio.run(service.add_history(55, bad))
    with pytest.raises(NotFoundError):
        asyncio.run(service.add_history(56, AddReturnHistoryRequest(status_id=2)))


# ═══ API ═══


def test_return_from_order_line_with_history_and_quantity_limit(api, db):
    for status_id, name in ((WAITING, "Очікує"), (RECEIVED, "Отримано")):
        insert(db, "dd_return_status", return_status_id=status_id, language_id=LANGUAGE_ID, name=name)
    insert(db, "dd_return_reason", return_reason_id=WRONG_SIZE, language_id=LANGUAGE_ID, name="Не підійшов розмір")
    insert(db, "dd_return_action", return_action_id=REFUNDED, language_id=LANGUAGE_ID, name="Повернено кошти")
    manager = api(MANAGER)

    lookups = manager.get("/returns/lookups").json()
    assert [s["name"] for s in lookups["statuses"]] == ["Очікує", "Отримано"]
    # The order ORDER_ID has 2 dresses: one comes back.
    body = {"orderId": ORDER_ID, "productId": PRODUCT_ID, "quantity": 1, "reasonId": WRONG_SIZE, "statusId": WAITING}
    created = manager.post("/returns", json=body | {"comment": "Мала"})
    assert created.status_code == 201
    return_id = created.json()["id"]
    history = {"statusId": RECEIVED, "comment": "Посилку отримали", "actionId": REFUNDED}
    assert manager.post(f"/returns/{return_id}/history", json=history).status_code == 204

    details = manager.get(f"/returns/{return_id}").json()
    keys = ("orderId", "product", "model", "quantity", "statusName", "actionName", "reasonName")
    assert [details[k] for k in keys] == [
        ORDER_ID, "Сукня", "T-1", 1, "Отримано", "Повернено кошти", "Не підійшов розмір",
    ]  # fmt: skip
    assert [h["comment"] for h in details["history"]] == ["Мала", "Посилку отримали"]
    [item] = manager.get(f"/returns?orderId={ORDER_ID}").json()["items"]
    assert item["id"] == return_id
    # The second dress can still come back, a third one does not exist.
    assert manager.post("/returns", json=body | {"quantity": 2}).status_code == 400
    assert manager.get("/returns/9999").status_code == 404

    assert api(STOREKEEPER).get("/returns").status_code == 403
