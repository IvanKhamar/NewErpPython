from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.application.locations import LocationDto
from newerp.domain.locations import ShopLocationData
from newerp.infrastructure.models.delivery import DdWarehouse


class SqlLocationStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_all(self, search: str | None) -> list[LocationDto]:
        query = select(DdWarehouse).order_by(DdWarehouse.warehouse_id)
        if search:
            query = query.where(
                or_(
                    DdWarehouse.name.contains(search, autoescape=True),
                    DdWarehouse.city.contains(search, autoescape=True),
                    DdWarehouse.address.contains(search, autoescape=True),
                    DdWarehouse.type.contains(search, autoescape=True),
                )
            )
        return [_dto(w) for w in await self._session.scalars(query)]

    async def get(self, location_id: int) -> LocationDto | None:
        warehouse = await self._session.get(DdWarehouse, location_id)
        return None if warehouse is None else _dto(warehouse)

    async def add(self, data: ShopLocationData) -> int:
        warehouse = DdWarehouse(image="", **_values(data))
        self._session.add(warehouse)
        await self._session.commit()
        return warehouse.warehouse_id

    async def update(self, location_id: int, data: ShopLocationData) -> bool:
        # The image is not changed by NewErp: it is uploaded in the site's admin.
        result = await self._session.execute(
            update(DdWarehouse).where(DdWarehouse.warehouse_id == location_id).values(**_values(data))
        )
        await self._session.commit()
        return result.rowcount > 0

    async def delete(self, location_id: int) -> bool:
        result = await self._session.execute(delete(DdWarehouse).where(DdWarehouse.warehouse_id == location_id))
        await self._session.commit()
        return result.rowcount > 0

    async def existing_ids(self, ids: list[int]) -> set[int]:
        if not ids:
            return set()
        return set(
            await self._session.scalars(select(DdWarehouse.warehouse_id).where(DdWarehouse.warehouse_id.in_(ids)))
        )


def _values(d: ShopLocationData) -> dict[str, str]:
    return {
        "name": d.name,
        "description": d.description,
        "contact": d.contact,
        "telephone": d.telephone,
        "city": d.city,
        "address": d.address,
        "type": d.type,
    }


def _dto(w: DdWarehouse) -> LocationDto:
    return LocationDto(
        id=w.warehouse_id, name=w.name, description=w.description, contact=w.contact, telephone=w.telephone,
        city=w.city, address=w.address, type=w.type,
    )  # fmt: skip
