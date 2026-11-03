"""Catalog maintenance of the old admin's console (Python first, item 52): the size filters follow the sizes.

The storefront filters products by size through dd_product_filter, but a product gets its sizes in the form or from
production, so the filter values drift. The rebuild makes every product have exactly the size filters of its sizes
(creating a missing filter value), like the old updateSizeFilters, which only added and never removed.
The other console task, recounting pre-order quantities, is POST /preorders/settle and its Celery task."""

import logging
from typing import Protocol

from newerp.application.common import Dto

audit = logging.getLogger("newerp.audit")


class SizeFilterResult(Dto):
    created_values: int  # new filter values ("XL" appeared for the first time)
    linked: int
    unlinked: int


class MaintenanceStore(Protocol):
    async def product_sizes(self) -> list[tuple[int, str]]:
        """(product id, size name) of every product size."""
        ...

    async def size_filters(self) -> dict[str, int]:
        """The size filter group's values: name → filter id."""
        ...

    async def create_size_filter(self, name: str) -> int: ...

    async def size_links(self) -> set[tuple[int, int]]:
        """(product id, filter id) links to the size filter group."""
        ...

    async def change_links(self, add: set[tuple[int, int]], remove: set[tuple[int, int]]) -> None: ...


class CatalogMaintenanceService:
    def __init__(self, store: MaintenanceStore) -> None:
        self._store = store

    async def rebuild_size_filters(self, user_id: int) -> SizeFilterResult:
        filters = await self._store.size_filters()
        wanted: set[tuple[int, int]] = set()
        created = 0
        for product_id, size in await self._store.product_sizes():
            name = size.strip()
            if not name:
                continue
            if name not in filters:
                filters[name] = await self._store.create_size_filter(name)
                created += 1
            wanted.add((product_id, filters[name]))
        current = await self._store.size_links()
        add, remove = wanted - current, current - wanted
        await self._store.change_links(add, remove)
        audit.info("size filters rebuilt: +%s values, +%s links, -%s links, by user %s",
                   created, len(add), len(remove), user_id)  # fmt: skip
        return SizeFilterResult(created_values=created, linked=len(add), unlinked=len(remove))
