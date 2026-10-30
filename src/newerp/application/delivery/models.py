from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import computed_field

from newerp.application.common import Dto, Money
from newerp.domain.delivery import ShipmentDocument, ShipmentTone


class ShipmentDto(Dto):
    """An order's waybill. tone: how to colour the status (Info, Success, Warning, Error)."""

    id: int
    order_id: int
    carrier: str
    number: str
    cost: Money
    status: str
    status_code: int
    tone: ShipmentTone
    is_final: bool
    recipient_address: str
    redelivery: bool
    date_created: datetime | None
    date_scheduled: date | None
    date_payed_keeping: date | None

    @staticmethod
    def of(d: ShipmentDocument) -> "ShipmentDto":
        return ShipmentDto(
            id=d.id, order_id=d.order_id, carrier=d.carrier, number=d.number, cost=d.cost, status=d.status,
            status_code=d.status_code, tone=d.tone, is_final=d.is_final, recipient_address=d.recipient_address,
            redelivery=d.redelivery, date_created=d.date_created, date_scheduled=d.date_scheduled,
            date_payed_keeping=d.date_payed_keeping,
        )  # fmt: skip


class TrackingRefreshResult(Dto):
    """client_configured False: no Nova Poshta API key, so nothing was checked (as it should be locally)."""

    client_configured: bool
    checked: int
    updated: int


class ReminderDto(Dto):
    """The parcel waits at the branch and storage soon becomes paid: worth reminding the customer."""

    order_id: int
    customer: str
    telephone: str
    number: str
    status: str
    date_payed_keeping: date
    manager_name: str | None


@dataclass(frozen=True)
class TrackingRequest:
    """What tracking needs: the waybill number and the recipient's phone (with it the API gives full data)."""

    number: str
    phone: str


@dataclass(frozen=True)
class OpenShipment:
    document: ShipmentDocument
    phone: str


class SenderOption(Dto):
    """A sender from the settings. Its API key never gets here: it stays on the server."""

    id: str
    name: str
    is_default: bool


class DropoffPointOption(Dto):
    """Where parcels are taken to be sent from."""

    id: str
    name: str
    is_default: bool


class CityDto(Dto):
    ref: str
    name: str


class WarehouseDto(Dto):
    ref: str
    name: str
    category: str
    max_weight: int


class ShipmentSeat(Dto):
    """A seat (box) of a shipment: weight in kg, sizes in cm."""

    weight: Decimal = Decimal(0)
    width: Decimal = Decimal(0)
    length: Decimal = Decimal(0)
    height: Decimal = Decimal(0)

    @computed_field
    @property
    def volume(self) -> Money:
        """Volume in m³, as the Nova Poshta API expects it (volumetricVolume)."""
        return self.width * self.length * self.height / Decimal(1_000_000)


class SeatPreset(Dto):
    """Ready boxes of the old site (the "small / medium / large" buttons)."""

    name: str
    seat: ShipmentSeat


SEAT_PRESETS = [
    SeatPreset(name="Маленька", seat=ShipmentSeat(weight=1, width=31, length=36, height=6)),
    SeatPreset(name="Середня", seat=ShipmentSeat(weight=1, width=41, length=48, height=8)),
    SeatPreset(name="Велика", seat=ShipmentSeat(weight=1, width=49, length=61, height=14)),
]


class ShipmentFormDto(Dto):
    """
    A waybill form filled in from the order (city and branch refs when found in the directory). The declared cost is
    the order total; cash on delivery is the total minus the advance when the payment is on receipt.
    can_create False: no API key and test waybills are off; test_mode: no key, only test waybills are made.
    """

    first_name: str
    last_name: str
    phone: str
    city_name: str
    city: CityDto | None
    warehouse_name: str
    warehouse: WarehouseDto | None
    declared_cost: Money
    cash_on_delivery: Money
    senders: list[SenderOption]
    dropoff_points: list[DropoffPointOption]
    seat_presets: list[SeatPreset]
    can_create: bool
    test_mode: bool


class CreateShipmentRequest(Dto):
    """sender_id / dropoff_point_id None: the defaults from the settings. cash_on_delivery 0: none."""

    first_name: str | None = None
    last_name: str | None = None
    middle_name: str | None = None
    phone: str | None = None
    city_ref: str | None = None
    warehouse_ref: str | None = None
    declared_cost: Decimal = Decimal(0)
    cash_on_delivery: Decimal = Decimal(0)
    sender_pays: bool = False
    seats: list[ShipmentSeat] | None = None
    sender_id: str | None = None
    dropoff_point_id: str | None = None


@dataclass(frozen=True)
class NewShipment:
    """Everything the Nova Poshta API needs for a new waybill (checked, with a normalized phone)."""

    sender_id: str
    dropoff_point_id: str
    order_id: int
    ship_date: date
    first_name: str
    last_name: str
    middle_name: str
    phone: str
    city_ref: str
    warehouse_ref: str
    declared_cost: Decimal
    cash_on_delivery: Decimal
    sender_pays: bool
    seats: list[ShipmentSeat]


@dataclass(frozen=True)
class CreatedShipment:
    ref: str
    number: str
    cost: Decimal


class ShipmentPrintKind(StrEnum):
    """Query value as .NET binds the enum: by name."""

    DOCUMENT = "Document"  # the waybill
    MARKINGS = "Markings"  # the sticker for the box


class WarehouseRefreshResult(Dto):
    """client_configured False: no key, the directory was not changed."""

    client_configured: bool
    count: int


@dataclass(frozen=True)
class OrderShippingInfo:
    """Order data the waybill form is filled from. prepayment: the advance received before shipping."""

    first_name: str
    last_name: str
    telephone: str
    city: str
    warehouse: str
    total: Decimal
    payment_code: str
    prepayment: Decimal = Decimal(0)


@dataclass(frozen=True)
class StoredShipment:
    """A waybill with the id of the sender it was made from (None: made on the old site)."""

    document: ShipmentDocument
    sender_id: str | None
