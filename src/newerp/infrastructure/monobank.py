"""monobank acquiring: the invoices table of the old site's module (dd_mono_orders) and the merchant API.

The old module turned TLS certificate checks off and waited for an answer forever; this client keeps httpx's checks
and a timeout, and the token travels only in the X-Token header, never in a URL or a log."""

import uuid
from datetime import datetime
from decimal import Decimal

import httpx
from sqlalchemy import Column, DateTime, Integer, MetaData, Numeric, String, Table, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.application.common import KYIV
from newerp.application.monobank import CreatedMonoInvoice, MonoInvoice, MonoInvoiceDto, invoice_dto
from newerp.config import MonobankSettings
from newerp.domain.common import DomainError
from newerp.domain.payments import MONO_FINAL, MonoState

TIMEOUT_SECONDS = 30
UAH = 980  # ISO 4217 code of the hryvnia, as the API wants it

metadata = MetaData()
mono_orders = Table(
    "dd_mono_orders",
    metadata,
    Column("Id", Integer, primary_key=True),
    Column("InvoiceId", String(50)),
    Column("OrderId", Integer),
    Column("SecretKey", String(51)),
    Column("status", String(32)),
    Column("amount", Numeric(15, 4)),
    Column("createdDate", DateTime),
    Column("modifiedDate", DateTime),
)


class SqlMonoInvoiceStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def for_order(self, order_id: int) -> list[MonoInvoiceDto]:
        m = mono_orders.c
        rows = await self._session.execute(
            select(m.InvoiceId, m.status, m.amount, m.createdDate, m.modifiedDate)
            .where(m.OrderId == order_id)
            .order_by(m.Id.desc())
        )
        return [invoice_dto(order_id, r[0] or "", r[1] or "", r[2] or Decimal(0), r[3], r[4]) for r in rows]

    async def add(self, order_id: int, invoice_id: str, amount: Decimal, now: datetime) -> None:
        await self._session.execute(
            mono_orders.insert().values(
                InvoiceId=invoice_id,
                OrderId=order_id,
                SecretKey="",
                status=MonoState.CREATED,
                amount=amount,
                createdDate=now,
                modifiedDate=now,
            )
        )
        await self._session.commit()

    async def unfinished_since(self, since: datetime) -> list[tuple[str, str]]:
        m = mono_orders.c
        rows = await self._session.execute(
            select(m.InvoiceId, m.status).where(
                m.InvoiceId.is_not(None), m.createdDate >= since, m.status.not_in([s.value for s in MONO_FINAL])
            )
        )
        return [(invoice_id, status or "") for invoice_id, status in rows]

    async def update(self, invoice: MonoInvoice) -> None:
        await self._session.execute(
            update(mono_orders)
            .where(mono_orders.c.InvoiceId == invoice.invoice_id)
            .values(status=invoice.status, amount=Decimal(invoice.amount) / 100, modifiedDate=invoice.modified)
        )
        await self._session.commit()

    async def remove_unpaid_duplicates(self, before: datetime) -> int:
        # Two steps: MySQL cannot delete from a table while a subquery reads the same table.
        m = mono_orders.c
        paid = list(await self._session.scalars(select(m.OrderId).where(m.status == MonoState.SUCCESS).distinct()))
        if not paid:
            return 0
        result = await self._session.execute(
            delete(mono_orders).where(m.status != MonoState.SUCCESS, m.createdDate < before, m.OrderId.in_(paid))
        )
        await self._session.commit()
        return result.rowcount


class HttpMonobankGateway:
    def __init__(self, settings: MonobankSettings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._transport = transport

    is_test = False

    async def create_invoice(
        self, amount_kopecks: int, reference: str, destination: str, redirect_url: str
    ) -> CreatedMonoInvoice:
        body = {
            "amount": amount_kopecks,
            "ccy": UAH,
            "merchantPaymInfo": {"reference": reference, "destination": destination},
        }
        if redirect_url:
            body["redirectUrl"] = redirect_url
        data = await self._call("POST", "/api/merchant/invoice/create", json=body)
        if not data.get("invoiceId") or not data.get("pageUrl"):
            raise DomainError("monobank не повернув рахунок.")
        return CreatedMonoInvoice(invoice_id=str(data["invoiceId"]), page_url=str(data["pageUrl"]))

    async def invoice(self, invoice_id: str) -> MonoInvoice:
        data = await self._call("GET", "/api/merchant/invoice/status", params={"invoiceId": invoice_id})
        return MonoInvoice(
            invoice_id=str(data.get("invoiceId") or invoice_id),
            status=str(data.get("status") or ""),
            amount=int(data.get("amount") or 0),
            created=_when(data.get("createdDate")),
            modified=_when(data.get("modifiedDate")),
        )

    async def _call(self, method: str, path: str, **kwargs) -> dict:
        token = self._settings.token.get_secret_value() if self._settings.token else ""
        url = f"{self._settings.base_url.rstrip('/')}{path}"
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, transport=self._transport) as http:
            response = await http.request(method, url, headers={"X-Token": token}, **kwargs)
        try:
            data = response.json()
        except ValueError:
            data = {}
        if not response.is_success or not isinstance(data, dict):
            reason = data.get("errText") if isinstance(data, dict) else None
            raise DomainError(f"monobank відмовив ({response.status_code}): {reason or 'невідома помилка'}.")
        return data


class StubMonobankGateway:
    """Without a token: an invoice with a made-up id and an address no browser can open (.invalid)."""

    is_test = True

    async def create_invoice(
        self, amount_kopecks: int, reference: str, destination: str, redirect_url: str
    ) -> CreatedMonoInvoice:
        invoice_id = f"test-{uuid.uuid4().hex[:16]}"
        return CreatedMonoInvoice(invoice_id=invoice_id, page_url=f"https://pay.monobank.invalid/{invoice_id}")

    async def invoice(self, invoice_id: str) -> MonoInvoice:
        return MonoInvoice(invoice_id=invoice_id, status=MonoState.CREATED, amount=0, created=None, modified=None)


def monobank_gateway(settings: MonobankSettings) -> HttpMonobankGateway | StubMonobankGateway:
    return HttpMonobankGateway(settings) if settings.is_configured else StubMonobankGateway()


def _when(value: object) -> datetime | None:
    """ "2026-11-03T10:00:00Z" as a local time without a zone, like the other dates of the database."""
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment.astimezone(KYIV).replace(tzinfo=None) if moment.tzinfo else moment
