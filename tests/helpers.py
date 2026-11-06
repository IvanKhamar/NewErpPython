"""Small helpers shared by the API tests."""

import io
from datetime import date, datetime

from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

from newerp.application.common import SystemClock


def create(client: TestClient, url: str, body: dict) -> int:
    """POSTs and returns the id from a 201 answer {"id": ...}."""
    response = client.post(url, json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def upload(client: TestClient, url: str, content: bytes):
    return client.post(url, content=content, headers={"Content-Type": "application/octet-stream"})


def open_book(content: bytes) -> Workbook:
    return load_workbook(io.BytesIO(content))


def save_book(book: Workbook) -> bytes:
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def now() -> datetime:
    """The time the app sees (Kyiv), not the machine's: on a UTC machine their dates differ for two hours a day."""
    return SystemClock().now()


def today() -> date:
    return now().date()
