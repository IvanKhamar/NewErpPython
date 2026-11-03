"""Shop catalog: products (/catalog/products) and their order on the storefront (/catalog/sort)."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, UploadFile

from newerp.api.deps import ClockDep, Session, enum_from_query
from newerp.api.security import CurrentPrincipal, Policies, require_roles
from newerp.application.catalog.dictionaries import (
    CatalogDictionaryService,
    CategoryEditDto,
    CategoryFormDto,
    CategoryListItem,
    LabelDto,
    SearchPresetDto,
)
from newerp.application.catalog.prices import (
    MAX_IMPORT_BYTES,
    ImportPreview,
    ImportResult,
    PriceChangePreview,
    PriceChangeRequest,
    PriceChangeResult,
    PriceChangeService,
    ProductImportService,
)
from newerp.application.catalog.product_edit import (
    CreatedProductDto,
    ProductEditDto,
    ProductEditService,
    ProductFormLookups,
    RemoveNewRequest,
    RemoveNewResult,
    SaveProductRequest,
)
from newerp.application.catalog.products import (
    ProductCardDto,
    ProductFilter,
    ProductListItem,
    ProductService,
    UpdateProductRequest,
)
from newerp.application.catalog.references import (
    CatalogReferenceService,
    DownloadDto,
    ManufacturerDto,
    ReferenceGroupDto,
    ReferenceItemDto,
    ReferenceKind,
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
from newerp.infrastructure.catalog.dictionaries import SqlDictionaryStore
from newerp.infrastructure.catalog.prices import SqlImportStore, SqlPriceStore
from newerp.infrastructure.catalog.product_edit import SqlProductEditStore
from newerp.infrastructure.catalog.products import SqlProductStore
from newerp.infrastructure.catalog.references import SqlReferenceStore
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


def get_product_edit_service(session: Session, clock: ClockDep) -> ProductEditService:
    return ProductEditService(SqlProductEditStore(session, get_settings().language_id), clock)


Products = Annotated[ProductService, Depends(get_product_service)]
Editor = Annotated[ProductEditService, Depends(get_product_edit_service)]
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


@products_router.get("/form-lookups", summary="Довідники форми товару: категорії, мітки, фільтри, розміри")
async def form_lookups(editor: Editor) -> ProductFormLookups:
    return await editor.lookups()


@products_router.post("", status_code=201, summary="Новий товар (Python first): вимкнений, доки його не ввімкнуть")
async def create_product(
    request: SaveProductRequest, principal: CurrentPrincipal, editor: Editor, response: Response
) -> CreatedProductDto:
    created = await editor.create(request, principal.user_id)
    response.headers["Location"] = f"/catalog/products/{created.product_id}"
    return created


@products_router.post("/remove-new", summary="Зняти NEW (мітку, фільтр «За темою» і тег) з усіх товарів принта за SKU")
async def remove_new(request: RemoveNewRequest, editor: Editor) -> RemoveNewResult:
    return await editor.remove_new(request)


@products_router.get("/{product_id}/edit", summary="Увесь товар для форми редагування (Python first)")
async def get_product_form(product_id: int, editor: Editor) -> ProductEditDto:
    return await editor.get(product_id)


@products_router.put(
    "/{product_id}",
    summary="Зберегти всю форму товару; HTML очищується; dateModified обов'язковий, змінений товар — 409",
)
async def save_product(
    product_id: int, request: SaveProductRequest, principal: CurrentPrincipal, editor: Editor
) -> ProductEditDto:
    return await editor.save(product_id, request, principal.user_id)


@products_router.post("/{product_id}/copy", status_code=201, summary="Копія товару: вимкнена, без залишку й SEO-адреси")
async def copy_product(product_id: int, principal: CurrentPrincipal, editor: Editor) -> CreatedProductDto:
    return await editor.copy(product_id, principal.user_id)


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


# ═══ Mass prices and the CSV import (Python first; the admin only) ═══

bulk_access = [Depends(require_roles(*Policies.CATALOG_BULK))]
prices_router = APIRouter(prefix="/catalog/prices", tags=["Масові ціни"], dependencies=bulk_access)


def get_price_service(session: Session) -> PriceChangeService:
    return PriceChangeService(SqlPriceStore(session, get_settings().language_id))


def get_import_service(session: Session) -> ProductImportService:
    return ProductImportService(SqlImportStore(session, get_settings().language_id))


Prices = Annotated[PriceChangeService, Depends(get_price_service)]
Imports = Annotated[ProductImportService, Depends(get_import_service)]


async def read_upload(file: UploadFile) -> bytes:
    """At most one byte over the limit is read, so a huge file is refused without filling the memory."""
    return await file.read(MAX_IMPORT_BYTES + 1)


@prices_router.post("/preview", summary="Що зміниться: ціни чи відсоток акції товарів обраних категорій і виробників")
async def preview_prices(request: PriceChangeRequest, prices: Prices) -> PriceChangePreview:
    return await prices.preview(request)


@prices_router.post("/apply", summary="Змінити ціни чи акції (кожна зміна — в журнал аудиту)")
async def apply_prices(request: PriceChangeRequest, principal: CurrentPrincipal, prices: Prices) -> PriceChangeResult:
    return await prices.apply(request, principal.user_id)


@products_router.post(
    "/import/preview", summary="CSV (UTF-8; model або product_id + колонки): що зміниться", dependencies=bulk_access
)
async def preview_import(file: UploadFile, imports: Imports) -> ImportPreview:
    return await imports.preview(await read_upload(file))


@products_router.post(
    "/import", summary="Оновити товари з CSV; рядки з помилками пропускаються", dependencies=bulk_access
)
async def apply_import(file: UploadFile, principal: CurrentPrincipal, imports: Imports) -> ImportResult:
    return await imports.apply(await read_upload(file), principal.user_id)


# ═══ Labels, search presets, categories (Python first) ═══

dictionaries_router = APIRouter(prefix="/catalog", tags=["Довідники каталогу"], dependencies=orders_access)


def get_dictionary_service(session: Session, clock: ClockDep) -> CatalogDictionaryService:
    return CatalogDictionaryService(SqlDictionaryStore(session, get_settings().language_id), clock)


Dictionaries = Annotated[CatalogDictionaryService, Depends(get_dictionary_service)]


@dictionaries_router.get("/labels", summary="Мітки товарів з кількістю товарів")
async def list_labels(service: Dictionaries) -> list[LabelDto]:
    return await service.labels()


@dictionaries_router.post("/labels", status_code=201, summary="Нова мітка (кольори — #rrggbb)")
async def create_label(request: LabelDto, service: Dictionaries) -> LabelDto:
    return await service.save_label(None, request)


@dictionaries_router.put("/labels/{label_id}", summary="Змінити мітку")
async def update_label(label_id: int, request: LabelDto, service: Dictionaries) -> LabelDto:
    return await service.save_label(label_id, request)


@dictionaries_router.delete("/labels/{label_id}", status_code=204, summary="Видалити мітку й зняти її з товарів")
async def delete_label(label_id: int, principal: CurrentPrincipal, service: Dictionaries) -> None:
    await service.delete_label(label_id, principal.user_id)


@dictionaries_router.get("/search-presets", summary="Готові пошуки вітрини")
async def list_presets(service: Dictionaries) -> list[SearchPresetDto]:
    return await service.presets()


@dictionaries_router.post("/search-presets", status_code=201, summary="Новий пресет (адреса відносна або https)")
async def create_preset(request: SearchPresetDto, service: Dictionaries) -> SearchPresetDto:
    return await service.save_preset(None, request)


@dictionaries_router.put("/search-presets/{preset_id}", summary="Змінити пресет")
async def update_preset(preset_id: int, request: SearchPresetDto, service: Dictionaries) -> SearchPresetDto:
    return await service.save_preset(preset_id, request)


@dictionaries_router.delete("/search-presets/{preset_id}", status_code=204, summary="Видалити пресет")
async def delete_preset(preset_id: int, service: Dictionaries) -> None:
    await service.delete_preset(preset_id)


@dictionaries_router.get("/categories", summary="Усі категорії з батьками в назві й кількістю товарів")
async def list_categories(service: Dictionaries) -> list[CategoryListItem]:
    return await service.categories()


@dictionaries_router.post("/categories/repair", summary="Перебудувати дерево категорій (dd_category_path)",
                          dependencies=bulk_access)  # fmt: skip
async def repair_categories(service: Dictionaries) -> dict[str, int]:
    return {"categories": await service.repair_paths()}


@dictionaries_router.get("/categories/{category_id}", summary="Категорія для редагування")
async def get_category(category_id: int, service: Dictionaries) -> CategoryEditDto:
    return await service.category(category_id)


@dictionaries_router.post("/categories", status_code=201, summary="Нова категорія; HTML опису очищується")
async def create_category(
    request: CategoryFormDto, principal: CurrentPrincipal, service: Dictionaries
) -> CategoryEditDto:
    return await service.save_category(None, request, principal.user_id)


@dictionaries_router.put("/categories/{category_id}", summary="Змінити категорію (і перебудувати дерево)")
async def update_category(
    category_id: int, request: CategoryFormDto, principal: CurrentPrincipal, service: Dictionaries
) -> CategoryEditDto:
    return await service.save_category(category_id, request, principal.user_id)


@dictionaries_router.delete("/categories/{category_id}", status_code=204, dependencies=bulk_access,
                            summary="Видалити категорію без підкатегорій (лише адміністратор)")  # fmt: skip
async def delete_category(category_id: int, principal: CurrentPrincipal, service: Dictionaries) -> None:
    await service.delete_category(category_id, principal.user_id)


# ═══ Filters, attributes, options, manufacturers, downloads (Python first) ═══


def get_reference_service(session: Session) -> CatalogReferenceService:
    return CatalogReferenceService(SqlReferenceStore(session, get_settings().language_id))


References = Annotated[CatalogReferenceService, Depends(get_reference_service)]


@dictionaries_router.get("/references/{kind}", summary="Групи фільтрів, атрибутів чи опцій зі значеннями")
async def list_groups(kind: ReferenceKind, service: References) -> list[ReferenceGroupDto]:
    return await service.groups(kind)


@dictionaries_router.post("/references/{kind}", status_code=201, summary="Нова група (для опцій — і тип)")
async def create_group(kind: ReferenceKind, request: ReferenceGroupDto, service: References) -> ReferenceGroupDto:
    return await service.save_group(kind, None, request)


@dictionaries_router.put("/references/{kind}/{group_id}", summary="Змінити групу")
async def update_group(
    kind: ReferenceKind, group_id: int, request: ReferenceGroupDto, service: References
) -> ReferenceGroupDto:
    return await service.save_group(kind, group_id, request)


@dictionaries_router.delete("/references/{kind}/{group_id}", status_code=204,
                            summary="Видалити групу зі значеннями, якщо жоден товар їх не має")  # fmt: skip
async def delete_group(kind: ReferenceKind, group_id: int, principal: CurrentPrincipal, service: References) -> None:
    await service.delete_group(kind, group_id, principal.user_id)


@dictionaries_router.post("/references/{kind}/{group_id}/values", status_code=201, summary="Нове значення групи")
async def create_value(
    kind: ReferenceKind, group_id: int, request: ReferenceItemDto, service: References
) -> ReferenceGroupDto:
    return await service.save_item(kind, group_id, None, request)


@dictionaries_router.put("/references/{kind}/{group_id}/values/{value_id}", summary="Змінити значення")
async def update_value(
    kind: ReferenceKind, group_id: int, value_id: int, request: ReferenceItemDto, service: References
) -> ReferenceGroupDto:
    return await service.save_item(kind, group_id, value_id, request)


@dictionaries_router.delete("/references/{kind}/values/{value_id}", status_code=204,
                            summary="Видалити значення, якщо жоден товар його не має")  # fmt: skip
async def delete_value(kind: ReferenceKind, value_id: int, principal: CurrentPrincipal, service: References) -> None:
    await service.delete_item(kind, value_id, principal.user_id)


@dictionaries_router.get("/manufacturers", summary="Виробники з кількістю товарів")
async def list_manufacturers(service: References) -> list[ManufacturerDto]:
    return await service.manufacturers()


@dictionaries_router.post("/manufacturers", status_code=201, summary="Новий виробник")
async def create_manufacturer(request: ManufacturerDto, service: References) -> ManufacturerDto:
    return await service.save_manufacturer(None, request)


@dictionaries_router.put("/manufacturers/{manufacturer_id}", summary="Змінити виробника")
async def update_manufacturer(manufacturer_id: int, request: ManufacturerDto, service: References) -> ManufacturerDto:
    return await service.save_manufacturer(manufacturer_id, request)


@dictionaries_router.delete(
    "/manufacturers/{manufacturer_id}", status_code=204, summary="Видалити виробника без товарів"
)
async def delete_manufacturer(manufacturer_id: int, principal: CurrentPrincipal, service: References) -> None:
    await service.delete_manufacturer(manufacturer_id, principal.user_id)


@dictionaries_router.get("/downloads", summary="Файли для покупців (записи; самі файли — на вітрині)")
async def list_downloads(service: References) -> list[DownloadDto]:
    return await service.downloads()


@dictionaries_router.post("/downloads", status_code=201, summary="Нове завантаження (ім'я файлу без папок)")
async def create_download(request: DownloadDto, service: References) -> DownloadDto:
    return await service.save_download(None, request)


@dictionaries_router.put("/downloads/{download_id}", summary="Змінити завантаження")
async def update_download(download_id: int, request: DownloadDto, service: References) -> DownloadDto:
    return await service.save_download(download_id, request)


@dictionaries_router.delete("/downloads/{download_id}", status_code=204, summary="Видалити завантаження без товарів")
async def delete_download(download_id: int, principal: CurrentPrincipal, service: References) -> None:
    await service.delete_download(download_id, principal.user_id)
