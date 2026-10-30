"""The Nova Post International REST client (as international_client.php of the old site) and its stub."""

import json
import random
import re
import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from newerp.application.delivery.international import HsCodeDto
from newerp.application.delivery.models import CreatedShipment
from newerp.config import NovaPostInternationalSettings
from newerp.domain.common import DomainError
from newerp.domain.international import InternationalShipment

HS_CODE_SUGGESTIONS = 10
TIMEOUT_SECONDS = 30


class HttpNovaPostInternationalClient:
    """
    The API key is exchanged for a JWT (GET /clients/authorization), and the JWT goes into the Authorization header
    as is, without "Bearer". The JWT is kept and taken again once when the API answers 401 (it expired).
    """

    def __init__(self, settings: NovaPostInternationalSettings, transport: httpx.AsyncBaseTransport | None = None):
        self._settings = settings
        self._transport = transport
        self._jwt: str | None = None

    is_configured = True
    can_create_documents = True

    async def search_hs_codes(self, keyword: str, country_code: str) -> list[HsCodeDto]:
        params = {"country-code": country_code, "keyword": keyword, "fuzzy": "true", "locale": "uk",
                  "size": str(HS_CODE_SUGGESTIONS)}  # fmt: skip
        root = await self._authorized("GET", f"{self._settings.url}/dictionary/classifier", params=params)
        codes = []
        for item in root.get("items") or []:
            product = item.get("product") or {}
            local, english = _text(product, "currentLocal"), _text(product, "en")
            sub_category = _text(item.get("subCategory") or {}, "currentLocal")
            name = local or english
            label = name if not sub_category else sub_category if not name else f"{name} — {sub_category}"
            codes.append(HsCodeDto(hs_code=_text(item, "hsCode"), label=label, name_eng=english))
        return codes

    async def create(self, shipment: InternationalShipment) -> CreatedShipment:
        created = await self._authorized("POST", f"{self._settings.url}/shipments", json_body=self.payload(shipment))
        number = _text(created, "number")
        if not number:
            raise DomainError("Nova Post International не повернула номер накладної.")
        try:
            cost = Decimal(str(created["cost"])) if isinstance(created.get("cost"), (int, float, str)) else None
        except InvalidOperation:
            cost = None
        return CreatedShipment(
            ref=_text(created, "id") or number, number=number, cost=cost if cost is not None else shipment.declared_cost
        )

    async def delete(self, shipment_id: str) -> None:
        await self._authorized("DELETE", f"{self._settings.url}/shipments/{shipment_id}")

    def payload(self, s: InternationalShipment) -> dict[str, Any]:
        """The old site's request body, field for field."""
        sender_settings = self._settings.sender

        def sender_address() -> dict[str, str]:
            return {
                "city": sender_settings.city, "street": sender_settings.street,
                "postCode": sender_settings.post_code, "building": sender_settings.building,
                "flat": sender_settings.flat,
            }  # fmt: skip

        sender: dict[str, Any] = {
            "companyTin": sender_settings.company_tin, "companyName": sender_settings.company_name,
            "phone": re.sub(r"\D", "", sender_settings.phone), "email": sender_settings.email,
            "name": sender_settings.name, "countryCode": "UA", "addressParts": sender_address(),
        }  # fmt: skip
        # Nova Post support confirmed to the old site: a company sender also needs registrationAddressParts.
        if sender_settings.company_tin:
            sender["registrationAddressParts"] = sender_address() | {"region": "", "note": ""}

        to = s.recipient
        payload: dict[str, Any] = {
            "status": "ReadyToShip",
            "clientOrder": str(s.order_id),
            "note": f"Vidro order #{s.order_id}",
            "payerType": "Sender",
            "invoice": {
                "incoterm": s.terms.incoterm,
                "exportReason": s.terms.export_reason,
                "cost": float(s.declared_cost),
                "currency": s.currency,
                "payerFeesCustoms": s.terms.payer_fees_customs,
                "items": [
                    {
                        "hsCode": i.hs_code,
                        "name": i.name,
                        "nameEng": i.name_eng,
                        "actualWeight": i.weight_grams,
                        "measurementCode": "pieces",
                        "amount": float(i.amount),
                        "cost": float(i.cost),
                    }
                    for i in s.items
                ],
            },  # fmt: skip
            "parcels": [
                {
                    "cargoCategory": s.terms.cargo_category,
                    "parcelDescription": s.terms.parcel_description,
                    "insuranceCost": float(p.insurance_cost),
                    "rowNumber": p.row_number,
                    "width": p.width_mm,
                    "length": p.length_mm,
                    "height": p.height_mm,
                    "actualWeight": p.weight_grams,
                }
                for p in s.parcels()
            ],  # fmt: skip
            "sender": sender,
            "recipient": {
                "phone": to.phone,
                "email": to.email,
                "name": f"{to.first_name} {to.last_name}".strip(),
                "countryCode": to.country_code,
                "addressParts": {
                    "city": to.city,
                    "region": to.region,
                    "street": to.street,
                    "postCode": to.post_code,
                    "building": to.building,
                    "flat": to.flat,
                },
            },  # fmt: skip
        }
        if s.terms.delivery_type is not None:
            payload["deliveryType"] = s.terms.delivery_type
        return payload

    async def _authorized(self, method: str, url: str, params=None, json_body=None) -> dict:
        """A call with the JWT; on 401 a new one is taken and the call repeated. Other errors become DomainError."""
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, transport=self._transport) as http:
            response = await self._send(http, method, url, params, json_body, renew=False)
            if response.status_code == 401:
                response = await self._send(http, method, url, params, json_body, renew=True)
        if not response.is_success:
            raise DomainError(
                f"Nova Post International відмовила (HTTP {response.status_code}): {errors(response.text)}"
            )
        return _parse(response.text)

    async def _send(self, http: httpx.AsyncClient, method, url, params, json_body, renew: bool) -> httpx.Response:
        token = await self._token(http, renew)
        return await http.request(method, url, params=params, json=json_body, headers={"Authorization": token})

    async def _token(self, http: httpx.AsyncClient, renew: bool) -> str:
        if self._jwt is not None and not renew:
            return self._jwt
        response = await http.get(
            f"{self._settings.url}/clients/authorization",
            params={"apiKey": self._settings.api_key.get_secret_value()},
        )
        if not response.is_success:
            raise DomainError(f"Nova Post International не прийняла ключ API (HTTP {response.status_code}).")
        jwt = _text(_parse(response.text), "jwt")
        if not jwt:
            raise DomainError("Nova Post International не видала токен: перевірте ключ API.")
        self._jwt = jwt
        return jwt


def errors(body: str) -> str:
    """API errors: {"errors": {"field": "text"}} or {"error"/"message": "..."}."""
    try:
        root = _parse(body)
    except ValueError:
        return body[:300] if body.strip() else "без пояснення"
    found = root.get("errors") if isinstance(root, dict) else None
    if isinstance(found, dict) and found:
        return "; ".join((f"{key}: " if key else "") + _plain(value) for key, value in found.items())
    if isinstance(found, list) and found:
        return "; ".join(_plain(value) for value in found)
    message = _text(root, "error") or _text(root, "message")
    return message or "без пояснення"


def _parse(body: str) -> dict:
    return json.loads(body) if body.strip() else {}


def _plain(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def _text(node: dict, name: str) -> str:
    value = node.get(name) if isinstance(node, dict) else None
    if value is None or isinstance(value, (dict, list)):
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


class StubNovaPostInternationalClient:
    """
    Used without a key: asks no one. With fake_documents (development only) it "creates" TEST… waybills and offers
    one test code, so the whole path can be walked in the UI.
    """

    def __init__(self, fake_documents: bool = False) -> None:
        self._fake = fake_documents

    is_configured = False

    @property
    def can_create_documents(self) -> bool:
        return self._fake

    async def search_hs_codes(self, keyword: str, country_code: str) -> list[HsCodeDto]:
        if not self._fake:
            return []
        return [
            HsCodeDto(hs_code="6204491000", label="Сукні з інших текстильних матеріалів (заглушка)", name_eng="Dresses")
        ]

    async def create(self, shipment: InternationalShipment) -> CreatedShipment:
        if not self._fake:
            raise DomainError("Ключ API Nova Post International не задано, тож міжнародну накладну створити не можна.")
        number = "TEST" + f"{random.randrange(10_000_000_000):010d}"
        return CreatedShipment(ref=str(uuid.uuid4()), number=number, cost=shipment.declared_cost)

    async def delete(self, shipment_id: str) -> None:
        return None  # test waybills exist only here


def nova_post_international_client(settings: NovaPostInternationalSettings):
    if settings.configured:
        return HttpNovaPostInternationalClient(settings)
    return StubNovaPostInternationalClient(settings.fake_documents)
