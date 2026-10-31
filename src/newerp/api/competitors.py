"""Competitor prices: competitors, pages of our products on their sites, checks, history and the comparison."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from newerp.api.deps import ClockDep, Session
from newerp.api.security import Policies, require_roles
from newerp.application.competitors import (
    AddCompetitorLinkRequest,
    CompetitorCheckResult,
    CompetitorDto,
    CompetitorLinkDto,
    CompetitorPriceDto,
    CompetitorPriceRequest,
    CompetitorReportDto,
    CompetitorService,
    PageFetcher,
    SaveCompetitorRequest,
)
from newerp.config import get_settings
from newerp.infrastructure.competitors import HttpPageFetcher, SqlCompetitorStore

router = APIRouter(
    prefix="/competitors", tags=["Ціни конкурентів"], dependencies=[Depends(require_roles(*Policies.ORDERS))]
)


async def get_page_fetcher() -> AsyncIterator[PageFetcher]:
    """A dependency with yield: the code after yield runs when the request ends, like a scoped IDisposable."""
    async with HttpPageFetcher() as fetcher:
        yield fetcher


def get_service(
    session: Session, clock: ClockDep, fetcher: Annotated[PageFetcher, Depends(get_page_fetcher)]
) -> CompetitorService:
    return CompetitorService(SqlCompetitorStore(session, get_settings().language_id), fetcher, clock)


Service = Annotated[CompetitorService, Depends(get_service)]


@router.get("", summary="Конкуренти з кількістю сторінок наших товарів")
async def list_competitors(service: Service) -> list[CompetitorDto]:
    return await service.competitors()


@router.post(
    "", status_code=201, summary="Додати конкурента: назва, сайт, необов'язковий вираз для ціни (перша група — ціна)"
)
async def create_competitor(request: SaveCompetitorRequest, service: Service, response: Response) -> CompetitorDto:
    competitor = await service.create_competitor(request)
    response.headers["Location"] = f"/competitors/{competitor.id}"
    return competitor


@router.put(
    "/{competitor_id}",
    summary="Змінити конкурента; неактивного не перевіряє щоденна перевірка й не показує порівняння",
)
async def update_competitor(competitor_id: int, request: SaveCompetitorRequest, service: Service) -> CompetitorDto:
    return await service.update_competitor(competitor_id, request)


@router.get("/links", summary="Сторінки наших товарів у конкурентів з останньою ціною й помилкою перевірки")
async def list_links(
    service: Service, product_id: Annotated[int | None, Query(alias="productId")] = None
) -> list[CompetitorLinkDto]:
    return await service.links(product_id)


@router.post(
    "/links",
    status_code=201,
    summary="Додати сторінку товару: адреса має бути на сайті конкурента, один товар — одна сторінка в конкурента",
)
async def add_link(request: AddCompetitorLinkRequest, service: Service, response: Response) -> CompetitorLinkDto:
    link = await service.add_link(request)
    response.headers["Location"] = f"/competitors/links/{link.id}"
    return link


@router.delete("/links/{link_id}", status_code=204, summary="Видалити сторінку разом з історією цін")
async def delete_link(link_id: int, service: Service) -> None:
    await service.delete_link(link_id)


@router.post("/links/{link_id}/price", summary="Вписати ціну вручну (для сайту, який не дає її прочитати)")
async def set_price(link_id: int, request: CompetitorPriceRequest, service: Service) -> CompetitorLinkDto:
    return await service.set_price(link_id, request)


@router.post("/links/{link_id}/check", summary="Прочитати ціну з цієї сторінки зараз")
async def check_link(link_id: int, service: Service) -> CompetitorLinkDto:
    return await service.check(link_id)


@router.get(
    "/links/{link_id}/history", summary="Історія цін сторінки: рядок, коли ціна змінилась, з джерелом auto чи manual"
)
async def history(link_id: int, service: Service) -> list[CompetitorPriceDto]:
    return await service.history(link_id)


@router.post(
    "/check",
    summary="Прочитати ціни всіх сторінок активних конкурентів (так само щодня робить фонова перевірка)",
)
async def check_all(service: Service) -> CompetitorCheckResult:
    return await service.check_all()


@router.get("/report", summary="Наша ціна проти останніх цін активних конкурентів: найнижча й різниця у відсотках")
async def report(service: Service) -> CompetitorReportDto:
    return await service.report()
