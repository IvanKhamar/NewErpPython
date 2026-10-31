"""Reseller partners and their XML feed."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response

from newerp.api.deps import ClockDep, Session
from newerp.api.security import Policies, require_roles
from newerp.application.common import CreatedId
from newerp.application.partners import (
    PartnerDto,
    PartnerFeedOptions,
    PartnerListItemDto,
    PartnerProductPickDto,
    PartnerService,
    SavePartnerRequest,
    ShopCategoryDto,
)
from newerp.config import get_settings
from newerp.infrastructure.partners import SqlPartnerRepository

router = APIRouter(prefix="/partners", tags=["Партнери"], dependencies=[Depends(require_roles(*Policies.ORDERS))])
# The partner's site downloads the feed, so it needs no login, as on the old site.
feed_router = APIRouter(tags=["Партнери"])


def get_repository(session: Session) -> SqlPartnerRepository:
    return SqlPartnerRepository(session, get_settings().language_id)


Repository = Annotated[SqlPartnerRepository, Depends(get_repository)]


def get_service(repository: Repository, clock: ClockDep) -> PartnerService:
    shop = get_settings().partner_feed
    return PartnerService(repository, PartnerFeedOptions(shop.shop_name, shop.company, shop.store_url), clock)


Service = Annotated[PartnerService, Depends(get_service)]


@router.get("", summary="Партнери-перепродавці з кількістю рядків відповідності")
async def list_partners(repository: Repository) -> list[PartnerListItemDto]:
    return await repository.list_all()


@router.get("/categories", summary="Категорії магазину з розмірами їхніх товарів")
async def categories(repository: Repository) -> list[ShopCategoryDto]:
    return await repository.list_categories()


@router.get("/products", summary="Пошук товарів за назвою чи id, з цінами розмірів")
async def search_products(service: Service, search: str | None = None) -> list[PartnerProductPickDto]:
    return await service.search_products(search)


@router.get("/{partner_id}", summary="Партнер з відповідністю категорій і переліком окремих товарів")
async def get_partner(partner_id: int, repository: Repository) -> PartnerDto:
    partner = await repository.get_dto(partner_id)
    return partner if partner is not None else Response(status_code=404)


@router.post("", status_code=201, summary="Новий партнер")
async def create_partner(request: SavePartnerRequest, service: Service, response: Response) -> CreatedId:
    partner_id = await service.create(request)
    response.headers["Location"] = f"/partners/{partner_id}"
    return CreatedId(id=partner_id)


@router.put(
    "/{partner_id}",
    status_code=204,
    summary="Змінити партнера разом з відповідностями (рядки з тією самою категорією чи товаром і розміром "
    "зберігають свій id)",
)
async def update_partner(partner_id: int, request: SavePartnerRequest, service: Service) -> None:
    await service.update(partner_id, request)


@router.delete("/{partner_id}", status_code=204, summary="Видалити партнера")
async def delete_partner(partner_id: int, service: Service) -> None:
    await service.delete(partner_id)


@feed_router.get(
    "/feeds/{code}.xml",
    summary="XML-фід партнера (yml_catalog) з його категоріями й цінами",
    response_class=Response,
    responses={200: {"content": {"application/xml": {}}}},
)
async def feed(code: str, service: Service) -> Response:
    xml = await service.build_feed_xml(code)
    if xml is None:
        return Response(status_code=404)
    return Response(xml, media_type="application/xml; charset=utf-8")
