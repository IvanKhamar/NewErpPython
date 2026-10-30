from datetime import date, datetime

from sqlalchemy import delete, exists, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.application.delivery.models import (
    CityDto,
    OpenShipment,
    OrderShippingInfo,
    ReminderDto,
    ShipmentDto,
    StoredShipment,
    WarehouseDto,
)
from newerp.domain.delivery import (
    FINAL_STATUS_CODES,
    INTERNATIONAL,
    WAITING_AT_BRANCH_CODES,
    NovaPoshtaWarehouse,
    ShipmentDocument,
)
from newerp.infrastructure.delivery.mapping import nova_poshta_warehouses, shipment_documents, shipment_senders
from newerp.infrastructure.models.orders import DdOrder, ErpOrderManager
from newerp.infrastructure.models.users import DdUser

INSERT_BATCH_SIZE = 1000  # the directory has tens of thousands of branches


class SqlShipmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._pending_senders: list[tuple[ShipmentDocument, str, int, datetime]] = []

    async def get_for_order(self, order_id: int) -> ShipmentDto | None:
        # One waybill per order; if there are several (made again), the latest is shown, as on the old site.
        document = await self._latest(order_id)
        return None if document is None else ShipmentDto.of(document)

    async def list_open_for_tracking(self) -> list[OpenShipment]:
        d = shipment_documents.c
        rows = await self._session.execute(
            select(ShipmentDocument, DdOrder.telephone)
            .join(DdOrder, DdOrder.order_id == d.order_id)
            # International waybills are unknown to the domestic API: asking about them only collects errors.
            .where(d.number != "", d.status_code.not_in(FINAL_STATUS_CODES), d.carrier != INTERNATIONAL)
        )
        return [OpenShipment(document, phone) for document, phone in rows]

    async def list_reminders(self, keeping_from: date, keeping_to: date) -> list[ReminderDto]:
        d = shipment_documents.c
        rows = await self._session.execute(
            select(
                DdOrder.order_id,
                DdOrder.firstname,
                DdOrder.lastname,
                DdOrder.telephone,
                d.number,
                d.status,
                d.date_payed_keeping,
                DdUser.firstname,
                DdUser.lastname,
            )  # fmt: skip
            .select_from(shipment_documents)
            .join(DdOrder, DdOrder.order_id == d.order_id)
            .outerjoin(ErpOrderManager, ErpOrderManager.order_id == DdOrder.order_id)
            .outerjoin(DdUser, DdUser.user_id == ErpOrderManager.user_id)
            .where(
                d.status_code.in_(WAITING_AT_BRANCH_CODES),
                d.date_payed_keeping.is_not(None),
                d.date_payed_keeping.between(keeping_from, keeping_to),
                DdOrder.date_remind.is_(None),
            )
            .order_by(d.date_payed_keeping)
        )
        return [
            ReminderDto(
                order_id=order_id,
                customer=f"{first} {last}".strip(),
                telephone=phone,
                number=number,
                status=status,
                date_payed_keeping=keeping,
                manager_name=None if manager_first is None else f"{manager_first} {manager_last}".strip(),
            )  # fmt: skip
            for order_id, first, last, phone, number, status, keeping, manager_first, manager_last in rows
        ]

    async def mark_reminded(self, order_id: int, reminded_at: datetime) -> bool:
        result = await self._session.execute(
            update(DdOrder).where(DdOrder.order_id == order_id).values(date_remind=reminded_at)
        )
        await self._session.commit()
        return result.rowcount > 0

    async def get_stored(self, order_id: int) -> StoredShipment | None:
        document = await self._latest(order_id)
        if document is None:
            return None
        sender_id = await self._session.scalar(
            select(shipment_senders.c.sender_id).where(shipment_senders.c.shipment_id == document.id)
        )
        return StoredShipment(document, sender_id)

    async def get_order_shipping_info(self, order_id: int) -> OrderShippingInfo | None:
        o = await self._session.get(DdOrder, order_id)
        if o is None:
            return None
        # The recipient comes from the shipping address, or the buyer if it is empty. The old site writes the branch
        # into address_2.
        return OrderShippingInfo(
            first_name=o.shipping_firstname or o.firstname,
            last_name=o.shipping_lastname or o.lastname,
            telephone=o.telephone,
            city=o.shipping_city,
            warehouse=o.shipping_address_2 or o.shipping_address_1,
            total=o.total,
            payment_code=o.payment_code,
            prepayment=o.prepayment,
        )

    def add(self, document: ShipmentDocument, sender_id: str, user_id: int, created_at: datetime) -> None:
        self._session.add(document)
        # The sender row needs the new waybill's id, so it is written right after the waybill in save_changes.
        self._pending_senders.append((document, sender_id, user_id, created_at))

    async def delete(self, document_id: int) -> None:
        await self._session.execute(delete(shipment_senders).where(shipment_senders.c.shipment_id == document_id))
        await self._session.execute(delete(shipment_documents).where(shipment_documents.c.id == document_id))
        await self._session.commit()

    async def save_changes(self) -> None:
        await self._session.flush()
        for document, sender_id, user_id, created_at in self._pending_senders:
            await self._session.execute(
                insert(shipment_senders).values(
                    shipment_id=document.id, sender_id=sender_id, created_by_user_id=user_id, created_at=created_at
                )
            )
        self._pending_senders.clear()
        await self._session.commit()

    async def _latest(self, order_id: int) -> ShipmentDocument | None:
        d = shipment_documents.c
        return await self._session.scalar(
            select(ShipmentDocument).where(d.order_id == order_id).order_by(d.id.desc()).limit(1)
        )


class SqlWarehouseDirectory:
    """
    The branch directory in dd_novaposhta_warehouse. There is no table of cities: as on the old site, a city is
    the city_ref + city_description of its branches.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def search_cities(self, search: str, limit: int) -> list[CityDto]:
        w = nova_poshta_warehouses.c
        rows = await self._session.execute(
            select(w.city_ref, w.city_description)
            .where(w.status.is_(True), w.city_description.startswith(search, autoescape=True))
            .group_by(w.city_ref, w.city_description)
            .order_by(w.city_description)
            .limit(limit)
        )
        return [CityDto(ref=ref, name=name) for ref, name in rows]

    async def search_warehouses(self, city_ref: str, search: str, limit: int) -> list[WarehouseDto]:
        w = nova_poshta_warehouses.c
        query = select(w.ref, w.description, w.category_of_warehouse, w.weight).where(
            w.city_ref == city_ref, w.status.is_(True)
        )
        if search:
            query = query.where(w.description.contains(search, autoescape=True))
        rows = await self._session.execute(query.order_by(w.sort_order, w.description).limit(limit))
        return [WarehouseDto(ref=r, name=n, category=c, max_weight=m) for r, n, c, m in rows]

    async def find_city(self, name: str) -> CityDto | None:
        w = nova_poshta_warehouses.c
        row = (
            await self._session.execute(
                select(w.city_ref, w.city_description).where(w.status.is_(True), w.city_description == name).limit(1)
            )
        ).first()
        return None if row is None else CityDto(ref=row[0], name=row[1])

    async def find_warehouse(self, city_ref: str, name: str) -> WarehouseDto | None:
        w = nova_poshta_warehouses.c
        row = (
            await self._session.execute(
                select(w.ref, w.description, w.category_of_warehouse, w.weight)
                .where(w.city_ref == city_ref, w.status.is_(True), w.description == name)
                .limit(1)
            )
        ).first()
        return None if row is None else WarehouseDto(ref=row[0], name=row[1], category=row[2], max_weight=row[3])

    async def warehouse_exists(self, city_ref: str, warehouse_ref: str) -> bool:
        w = nova_poshta_warehouses.c
        return bool(
            await self._session.scalar(
                select(exists().where(w.ref == warehouse_ref, w.city_ref == city_ref, w.status.is_(True)))
            )
        )

    async def replace_all(self, warehouses: list[NovaPoshtaWarehouse]) -> None:
        # One transaction: the old site's storefront must not see an empty directory in the middle of the update.
        unique: dict[str, NovaPoshtaWarehouse] = {}
        for warehouse in warehouses:
            unique.setdefault(warehouse.ref, warehouse)  # the first one per ref, like DistinctBy
        rows = list(unique.values())
        await self._session.execute(delete(nova_poshta_warehouses))
        for start in range(0, len(rows), INSERT_BATCH_SIZE):
            batch = rows[start : start + INSERT_BATCH_SIZE]
            await self._session.execute(
                insert(nova_poshta_warehouses),
                [
                    {
                        "ref": w.ref,
                        "description": w.description,
                        "category_of_warehouse": w.category,
                        "city_ref": w.city_ref,
                        "city_description": w.city_description,
                        "weight": w.max_weight,
                        "sort_order": w.sort_order,
                        "status": w.status,
                    }  # fmt: skip
                    for w in batch
                ],
            )
        await self._session.commit()
