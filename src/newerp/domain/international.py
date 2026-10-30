"""A Nova Post International waybill, as the old site's "International" tab built it.

The recipient abroad, the customs invoice (HS code, English name and weight for every line) and the seats. Item weight
in grams, seat sizes in cm and kg; parcels() converts them to mm and grams for the API. The same as the Java version.
"""

import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from newerp.domain.common import DomainError

REGION_REQUIRED = frozenset({"US", "IE", "CA"})  # the API needs a state or region for these countries


@dataclass(frozen=True)
class Recipient:
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    email: str | None = None
    country_code: str | None = None
    region: str | None = None
    city: str | None = None
    street: str | None = None
    building: str | None = None
    flat: str | None = None
    post_code: str | None = None


def _or_default(value: str | None, fallback: str) -> str:
    return value.strip() if value and value.strip() else fallback


@dataclass(frozen=True)
class Terms:
    """Customs and delivery terms; empty ones take the old site's defaults. delivery_type only when chosen."""

    incoterm: str = "DAP"
    export_reason: str = "Selling"
    payer_fees_customs: str = "Recipient"
    cargo_category: str = "parcel"
    delivery_type: str | None = None
    parcel_description: str = "Printed apparel / merchandise"

    @staticmethod
    def of(
        incoterm: str | None = None, export_reason: str | None = None, payer_fees_customs: str | None = None,
        cargo_category: str | None = None, delivery_type: str | None = None, parcel_description: str | None = None,
    ) -> "Terms":  # fmt: skip
        return Terms(
            _or_default(incoterm, "DAP"),
            _or_default(export_reason, "Selling"),
            _or_default(payer_fees_customs, "Recipient"),
            _or_default(cargo_category, "parcel"),
            delivery_type.strip() if delivery_type and delivery_type.strip() else None,
            _or_default(parcel_description, "Printed apparel / merchandise"),
        )


@dataclass(frozen=True)
class Item:
    """A customs invoice line. weight_grams is the weight of the whole line, as the old form asked."""

    name: str | None
    name_eng: str | None
    hs_code: str | None
    weight_grams: int
    amount: Decimal | None
    cost: Decimal | None


@dataclass(frozen=True)
class Seat:
    """A box: sizes in cm, weight in kg."""

    weight: Decimal
    width: Decimal
    length: Decimal
    height: Decimal


@dataclass(frozen=True)
class Parcel:
    """A seat as the API wants it: sizes in mm, weight in grams, its share of the declared cost as insurance."""

    row_number: int
    width_mm: int
    length_mm: int
    height_mm: int
    weight_grams: int
    insurance_cost: Decimal


@dataclass(frozen=True)
class InternationalShipment:
    order_id: int
    recipient: Recipient
    terms: Terms
    items: list[Item]
    seats: list[Seat]
    declared_cost: Decimal
    currency: str = field(default="UAH")

    @staticmethod
    def of(
        order_id: int,
        recipient: Recipient | None,
        terms: Terms | None,
        items: list[Item] | None,
        seats: list[Seat] | None,
        declared_cost: Decimal | None,
        currency: str | None,
    ) -> "InternationalShipment":
        """Checks everything the old form checked and lists everything missing in one message."""
        missing: list[str] = []
        r = recipient or Recipient()
        for value, label in (
            (r.last_name, "Прізвище"),
            (r.first_name, "Ім'я"),
            (r.phone, "Телефон"),
            (r.city, "Населений пункт"),
            (r.street, "Вулиця"),
            (r.building, "Будинок"),
            (r.post_code, "Індекс"),
        ):
            if not value or not value.strip():
                missing.append(label)
        country = (r.country_code or "").strip().upper()
        if not re.fullmatch(r"[A-Z]{2}", country) or country == "UA":
            missing.append("Країна отримувача (не Україна)")
            country = ""
        if country in REGION_REQUIRED and not (r.region or "").strip():
            missing.append("Штат/область (обов'язково для США, Ірландії, Канади)")

        lines = items or []
        if not lines:
            missing.append("товари в замовленні")
        clean_lines = []
        for item in lines:
            label = item.name if item.name and item.name.strip() else "товар"
            hs_code = re.sub(r"\D", "", item.hs_code or "")
            if not hs_code:
                missing.append(f"код УКТЗЕД ({label})")
            if not (item.name_eng or "").strip():
                missing.append(f"англійська назва товару ({label})")
            if item.weight_grams <= 0:
                missing.append(f"вага товару в грамах ({label})")
            amount = item.amount if item.amount is not None and item.amount > 0 else Decimal(1)
            clean_lines.append(
                Item(
                    (item.name or "").strip(),
                    (item.name_eng or "").strip(),
                    hs_code,
                    item.weight_grams,
                    amount,
                    item.cost if item.cost is not None else Decimal(0),
                )  # fmt: skip
            )
        if declared_cost is None or declared_cost <= 0:
            missing.append("декларована вартість")
        if not seats:
            missing.append("параметри місця відправлення (розміри/вага)")
        if missing:
            raise DomainError(f"Не заповнені або некоректні поля: {', '.join(missing)}.")

        clean = Recipient(
            r.first_name.strip(), r.last_name.strip(), re.sub(r"\D", "", r.phone), (r.email or "").strip(), country,
            (r.region or "").strip(), r.city.strip(), r.street.strip(), r.building.strip(), (r.flat or "").strip(),
            r.post_code.strip(),
        )  # fmt: skip
        return InternationalShipment(
            order_id, clean, terms or Terms(), clean_lines, list(seats), declared_cost,
            (currency or "").strip().upper() or "UAH",
        )  # fmt: skip

    @property
    def items_weight_grams(self) -> int:
        return sum(i.weight_grams for i in self.items)

    def parcels(self) -> list[Parcel]:
        """
        Seats for the API as the old site counted them: cm × 10 = mm, the declared cost split evenly as insurance,
        and a single seat weighs what its items weigh (several seats: each its own weight in kg × 1000).
        """
        insurance = (self.declared_cost / len(self.seats)).quantize(Decimal("0.01"), ROUND_HALF_UP)
        single = len(self.seats) == 1
        return [
            Parcel(
                i + 1,
                _mm(seat.width),
                _mm(seat.length),
                _mm(seat.height),
                self.items_weight_grams if single else _round(seat.weight * 1000),
                insurance,
            )  # fmt: skip
            for i, seat in enumerate(self.seats)
        ]


def _round(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), ROUND_HALF_UP))


def _mm(cm: Decimal) -> int:
    return _round((cm if cm > 0 else Decimal(1)) * 10)
