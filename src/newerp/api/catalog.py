"""Shop catalog: products (/catalog/products) and their order on the storefront (/catalog/sort)."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from newerp.api.deps import ClockDep, Session, enum_from_query
from newerp.api.security import Policies, require_roles
from newerp.application.catalog.products import (
    ProductCardDto,
    ProductFilter,
    ProductListItem,
    ProductService,
    UpdateProductRequest,
)
from newerp.application.catalog.sort import (
    ApplySortRequest,
    CrossSellMetricDto,
    ProductSortOptions,
    ProductSortService,
    RecalculateGroupRequest,
    SalesPeriod,
    SetPinRequest,
    SortGroupDto,
    SortPinDto,
    SortPreviewRow,
    SortProductDto,
    SortResult,
    SortSnapshotDto,
)
from newerp.application.common import LookupItem, PagedResult
from newerp.config import get_settings
from newerp.domain.catalog import SortGroupType
from newerp.infrastructure.catalog.products import SqlProductStore
from newerp.infrastructure.catalog.sort import SqlProductSortStore

orders_access = [Depends(require_roles(*Policies.ORDERS))]
products_router = APIRouter(prefix="/catalog/products", tags=["Товари"], dependencies=orders_access)
sort_router = APIRouter(prefix="/catalog/sort", tags=["Порядок товарів"], dependencies=orders_access)


def get_product_service(session: Session, clock: ClockDep) -> ProductService:
    return ProductService(SqlProductStore(session, get_settings().language_id), clock)


def get_sort_service(session: Session, clock: ClockDep) -> ProductSortService:
    settings = get_settings()
    options = ProductSortOptions(**settings.product_sort.model_dump())
    return ProductSortService(SqlProductSortStore(session, settings.language_id, options, clock.now), options, clock)


Products = Annotated[ProductService, Depends(get_product_service)]
Sorting = Annotated[ProductSortService, Depends(get_sort_service)]


# ═══ Products ═══


@products_router.get("", summary="Товари магазину з фільтрами (пошук, категорія, мітка, увімкнені) і пагінацією")
async def list_products(
    service: Products,
    search: str | None = None,
    category_id: Annotated[int | None, Query(alias="categoryId")] = None,
    label_id: Annotated[int | None, Query(alias="labelId")] = None,
    enabled: bool | None = None,
    page: int = 1,
    page_size: Annotated[int, Query(alias="pageSize")] = 20,
) -> PagedResult[ProductListItem]:
    product_filter = ProductFilter(
        search=search, category_id=category_id, label_id=label_id, enabled=enabled, page=page, page_size=page_size
    )
    return await service.list_page(product_filter)


@products_router.get("/labels", summary="Усі мітки товарів")
async def labels(service: Products) -> list[LookupItem]:
    return await service.labels()


@products_router.get("/categories", summary="Увімкнені категорії")
async def categories(service: Products) -> list[LookupItem]:
    return await service.categories()


@products_router.get(
    "/{product_id}", summary="Картка товару: розміри із залишками (лише для читання), мітки й категорії"
)
async def get_product(product_id: int, service: Products) -> ProductCardDto:
    return await service.get(product_id)


@products_router.patch(
    "/{product_id}",
    summary="Змінити ціну, увімкнення, мітки або категорії (null — без змін); dateModified з картки обов'язковий, "
    "якщо товар змінили після цього — 409",
)
async def update_product(product_id: int, request: UpdateProductRequest, service: Products) -> ProductCardDto:
    return await service.update(product_id, request)


# ═══ Sort ═══


def group_type(type_: Annotated[str, Query(alias="type")]) -> SortGroupType:
    """?type= takes the number or the .NET name (Category)."""
    return enum_from_query(SortGroupType, type_)


@sort_router.get(
    "/groups",
    summary="Групи товарів у порядку перерахунку: теми, категорії (з кольорами окремо), мітки, панель допродажу",
)
async def groups(service: Sorting) -> list[SortGroupDto]:
    return await service.list_groups()


@sort_router.get(
    "/preview",
    summary="Новий порядок групи без запису: продажі за період (days або from–to), закріплені товари, поточна й "
    "нова позиція",
)
async def preview(
    service: Sorting,
    group: Annotated[SortGroupType, Depends(group_type)],
    key: str,
    days: int | None = None,
    from_: Annotated[date | None, Query(alias="from")] = None,
    to: date | None = None,
) -> list[SortPreviewRow]:
    return await service.preview(group, key, SalesPeriod(days=days, from_=from_, to=to))


@sort_router.post("/apply", summary="Записати готовий порядок групи (зі знімком попереднього)")
async def apply(request: ApplySortRequest, service: Sorting) -> SortResult:
    return await service.apply(request)


@sort_router.post("/recalculate", summary="Перерахувати й записати одну групу")
async def recalculate(request: RecalculateGroupRequest, service: Sorting) -> SortResult:
    return await service.recalculate_group(request)


@sort_router.post("/recalculate-all", summary="Перерахувати всі групи (один знімок перед початком)")
async def recalculate_all(service: Sorting, period: SalesPeriod | None = None) -> SortResult:
    return await service.recalculate_all(period)


@sort_router.get("/snapshots", summary="Останні 50 знімків порядку")
async def snapshots(service: Sorting) -> list[SortSnapshotDto]:
    return await service.list_snapshots()


@sort_router.post("/snapshots/{snapshot_id}/restore", status_code=204, summary="Повернути порядок товарів зі знімка")
async def restore_snapshot(snapshot_id: int, service: Sorting) -> None:
    await service.restore_snapshot(snapshot_id)


@sort_router.get("/pins", summary="Усі закріплені вручну позиції")
async def pins(service: Sorting) -> list[SortPinDto]:
    return await service.list_pins()


@sort_router.post(
    "/pins",
    status_code=204,
    summary="Закріпити товар на позиції в групі (попередній пін товару чи позиції замінюється)",
)
async def set_pin(request: SetPinRequest, service: Sorting) -> None:
    await service.set_pin(request)


@sort_router.delete("/pins/{pin_id}", status_code=204, summary="Зняти закріплення")
async def remove_pin(pin_id: int, service: Sorting) -> None:
    await service.remove_pin(pin_id)


@sort_router.get("/crosssell-metric", summary="Чим міряти популярність у панелі допродажу")
async def get_cross_sell_metric(service: Sorting) -> CrossSellMetricDto:
    return await service.get_cross_sell_metric()


@sort_router.put(
    "/crosssell-metric", summary="Популярність у панелі допродажу: panelOnly=true — лише продажі через панель"
)
async def set_cross_sell_metric(metric: CrossSellMetricDto, service: Sorting) -> CrossSellMetricDto:
    return await service.set_cross_sell_metric(metric)


@sort_router.get("/products", summary="Пошук увімкнених товарів за назвою (для закріплення)")
async def search_products(service: Sorting, search: str | None = None) -> list[SortProductDto]:
    return await service.search_products(search)
