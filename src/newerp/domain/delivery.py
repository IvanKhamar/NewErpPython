"""Nova Poshta delivery: waybills (TTN) of orders and the directory of branches."""

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import IntEnum


class ShipmentTone(IntEnum):
    """How a waybill status is coloured in the list, as the old site did (a number in JSON, like a .NET enum)."""

    INFO = 0  # on the way or at the branch
    SUCCESS = 1  # received (with a money transfer: when the money came back to the sender)
    WARNING = 2  # return or redirection
    ERROR = 3  # refused or deleted


@dataclass(frozen=True)
class TrackingInfo:
    """What Nova Poshta tracking reported about one waybill."""

    number: str
    status: str
    status_code: int
    recipient_address: str
    redelivery: bool
    date_created: datetime | None
    date_scheduled: date | None
    date_payed_keeping: date | None


# Codes after which a waybill no longer changes: received, refused, returned. Tracking skips them.
FINAL_STATUS_CODES = frozenset({9, 11, 102, 103, 104, 105, 107, 108})
# The parcel waits at the branch: for these the start of paid storage counts.
WAITING_AT_BRANCH_CODES = frozenset({7, 8})
NOVA_POSHTA = "novaposhta"
INTERNATIONAL = "international"  # Nova Post International (abroad); the domestic API does not track them


class ShipmentDocument:
    """A waybill of an order (dd_novaposhta_document of the old site)."""

    def __init__(
        self, order_id: int, number: str, cost: Decimal, document_ref: str | None = None, carrier: str = NOVA_POSHTA
    ) -> None:
        self.id: int | None = None
        self.order_id = order_id
        self.number = number  # 14 digits at Nova Poshta
        self.ref = document_ref or number  # the API Ref, needed to print and delete; the number if unknown
        self.cost = cost
        self.carrier = carrier  # empty or novaposhta: Nova Poshta; international: Nova Post International
        self.status = ""
        self.status_code = 0
        self.recipient_address = ""
        self.redelivery = False  # a return delivery exists (for example, a cash-on-delivery money transfer)
        self.date_created: datetime | None = None
        self.date_scheduled: date | None = None
        self.date_payed_keeping: date | None = None

    @property
    def is_final(self) -> bool:
        return self.status_code in FINAL_STATUS_CODES

    @property
    def tone(self) -> ShipmentTone:
        code = self.status_code
        if (code == 9 and not self.redelivery) or (code == 11 and self.redelivery) or code == 106:
            return ShipmentTone.SUCCESS
        if code in (3, 107):
            return ShipmentTone.ERROR
        if code in (102, 103, 105, 108):
            return ShipmentTone.WARNING
        return ShipmentTone.INFO

    def apply_tracking(self, info: TrackingInfo) -> bool:
        """Like the old site, changes the record only when the status code changed. True if it changed."""
        if info.status_code == self.status_code:
            return False
        self.status = info.status[:255]
        self.status_code = info.status_code
        self.recipient_address = info.recipient_address[:255]
        self.redelivery = info.redelivery
        self.date_created = info.date_created or self.date_created
        self.date_scheduled = info.date_scheduled
        self.date_payed_keeping = info.date_payed_keeping
        return True


# Categories shown as working: branch, partner store, mobile branch, parcel locker.
ACTIVE_CATEGORIES = frozenset({"Branch", "Store", "MobileBranch", "Postomat"})
_BRANCH_NUMBER = re.compile(r"№\s*(\d+)")


class NovaPoshtaWarehouse:
    """
    A branch or parcel locker from the Nova Poshta directory (dd_novaposhta_warehouse of the old site). The old
    site's storefront uses it too, so the sorting and "working" rules are the old code's.
    """

    def __init__(
        self,
        ref: str,
        description: str,
        category: str,
        city_ref: str,
        city_description: str,
        max_weight: int,
        working: bool,
    ) -> None:
        self.ref = ref
        self.description = description[:255]
        self.category = category  # CategoryOfWarehouse from the API: Branch, Postomat, Store…
        self.city_ref = city_ref
        self.city_description = city_description.strip()[:255]
        self.max_weight = max_weight  # the heaviest parcel, kg (0: no limit)
        # As the old site: the branch number from the name ("Відділення №12: …" → 12), so №2 goes before №10.
        match = _BRANCH_NUMBER.search(description)
        self.sort_order = int(match.group(1)) if match else 1
        self.status = working and category in ACTIVE_CATEGORIES  # works and accepts parcels
