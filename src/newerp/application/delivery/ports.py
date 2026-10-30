from datetime import date, datetime
from typing import Protocol

from newerp.application.delivery.models import (
    CityDto,
    CreatedShipment,
    DropoffPointOption,
    NewShipment,
    OpenShipment,
    OrderShippingInfo,
    ReminderDto,
    SenderOption,
    ShipmentDto,
    ShipmentPrintKind,
    StoredShipment,
    TrackingRequest,
    WarehouseDto,
)
from newerp.domain.delivery import NovaPoshtaWarehouse, ShipmentDocument, TrackingInfo


class NovaPoshtaClient(Protocol):
    """
    The Nova Poshta API. Without a key a stub is used that asks nothing, so local development never reaches the
    real service. Only the implementation knows the senders' API keys; outside there are only ids and names.
    """

    @property
    def is_configured(self) -> bool:
        """Whether a key is set, so this is the real client."""
        ...

    @property
    def can_create_documents(self) -> bool:
        """The real client, or the stub with test waybills (nova_poshta.fake_documents)."""
        ...

    @property
    def senders(self) -> list[SenderOption]: ...

    @property
    def dropoff_points(self) -> list[DropoffPointOption]: ...

    async def track(self, documents: list[TrackingRequest]) -> list[TrackingInfo]:
        """Waybill statuses (TrackingDocument.getStatusDocuments). Read only."""
        ...

    async def create(self, shipment: NewShipment) -> CreatedShipment:
        """Creates the recipient and the waybill (Counterparty.save + InternetDocument.save)."""
        ...

    async def delete(self, sender_id: str, document_ref: str) -> None:
        """Deletes a waybill at Nova Poshta with the key of the sender that made it."""
        ...

    async def print(self, sender_id: str, document_ref: str, kind: ShipmentPrintKind) -> bytes:
        """The PDF of a waybill or marking. The API key stays on the server; the browser gets only the file."""
        ...

    async def fetch_warehouses(self) -> list[NovaPoshtaWarehouse]:
        """The whole directory of branches and parcel lockers (Address.getWarehouses page by page)."""
        ...


class ShipmentRepository(Protocol):
    async def get_for_order(self, order_id: int) -> ShipmentDto | None: ...

    async def list_open_for_tracking(self) -> list[OpenShipment]:
        """Waybills with a number that may still change (not received, not returned), for a status change."""
        ...

    async def list_reminders(self, keeping_from: date, keeping_to: date) -> list[ReminderDto]:
        """Parcels at a branch whose paid storage starts in this range, with no reminder yet."""
        ...

    async def mark_reminded(self, order_id: int, reminded_at: datetime) -> bool:
        """False if there is no such order."""
        ...

    async def get_stored(self, order_id: int) -> StoredShipment | None:
        """The order's latest waybill for a change (print, delete) with its sender."""
        ...

    async def get_order_shipping_info(self, order_id: int) -> OrderShippingInfo | None: ...

    def add(self, document: ShipmentDocument, sender_id: str, user_id: int, created_at: datetime) -> None:
        """A new waybill and the sender it was made from (saved by save_changes)."""
        ...

    async def delete(self, document_id: int) -> None:
        """Deletes the waybill and its sender record at once."""
        ...

    async def save_changes(self) -> None: ...


class WarehouseDirectory(Protocol):
    """The directory of Nova Poshta cities and branches (a local copy shared with the old site)."""

    async def search_cities(self, search: str, limit: int) -> list[CityDto]:
        """Cities whose name starts with the search."""
        ...

    async def search_warehouses(self, city_ref: str, search: str, limit: int) -> list[WarehouseDto]:
        """Working branches of the city whose name contains the search (empty: all), by number."""
        ...

    async def find_city(self, name: str) -> CityDto | None:
        """A city by its exact name (as written in the order)."""
        ...

    async def find_warehouse(self, city_ref: str, name: str) -> WarehouseDto | None: ...

    async def warehouse_exists(self, city_ref: str, warehouse_ref: str) -> bool: ...

    async def replace_all(self, warehouses: list[NovaPoshtaWarehouse]) -> None:
        """Replaces the whole directory in one transaction."""
        ...
