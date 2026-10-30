from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from newerp.api.deps import ClockDep, Session
from newerp.api.errors import problem
from newerp.api.security import CurrentPrincipal, Policies, require_roles
from newerp.application.common import CreatedId
from newerp.application.production.models import (
    ProductionLogDto,
    ProductionLogFilter,
    ProductionModelDto,
    ProductionModelSummaryDto,
    ProductionOperation,
    ProductionOperationRequest,
    ProductionOperationResult,
    ProductionStocktakingRequest,
    ProductionStocktakingResult,
    SaveComponentRequest,
    SaveProductionModelRequest,
    SetComponentBomRequest,
    SetProductionLinksRequest,
    SetSewnBomRequest,
    SewingPlanDto,
    SewnByWarehouseDto,
    SewnTransferDto,
    SizePlacesDto,
    StorefrontSyncResult,
    TransferSewnRequest,
)
from newerp.application.production.ports import ProductionModelFilter
from newerp.application.production.services import ProductionLocationService, ProductionService, SewingPlanService
from newerp.config import get_settings
from newerp.infrastructure.locations import SqlLocationStore
from newerp.infrastructure.production.repositories import (
    SqlProductionRepository,
    SqlSewnLocationRepository,
    SqlStorefrontStock,
)

router = APIRouter(prefix="/production", tags=["Виробництво"], dependencies=[Depends(require_roles(*Policies.STOCK))])


def get_repository(session: Session) -> SqlProductionRepository:
    return SqlProductionRepository(session, get_settings().language_id)


Repository = Annotated[SqlProductionRepository, Depends(get_repository)]


def get_service(session: Session, repository: Repository, clock: ClockDep) -> ProductionService:
    storefront = SqlStorefrontStock(session, get_settings().language_id)
    locations = SqlSewnLocationRepository(session)
    return ProductionService(repository, storefront, locations, SqlLocationStore(session), clock)


def get_location_service(session: Session, repository: Repository, clock: ClockDep) -> ProductionLocationService:
    return ProductionLocationService(repository, SqlSewnLocationRepository(session), SqlLocationStore(session), clock)


def get_plan_service(repository: Repository, clock: ClockDep) -> SewingPlanService:
    return SewingPlanService(repository, get_settings().production.plan_status_ids, clock)


Service = Annotated[ProductionService, Depends(get_service)]
ByWarehouse = Annotated[ProductionLocationService, Depends(get_location_service)]


@router.get("/models", summary="Моделі з підсумками крою й пошиву; belowMinimumOnly=true — пошито менше за minTotal")
async def list_models(
    repository: Repository,
    type: str | None = None,
    below_minimum_only: Annotated[bool, Query(alias="belowMinimumOnly")] = False,
) -> list[ProductionModelSummaryDto]:
    return await repository.list_models(ProductionModelFilter(type, below_minimum_only))


@router.get(
    "/models/{production_id}",
    response_model=ProductionModelDto,
    summary="Модель з розмірами, деталями й специфікаціями",
)
async def get_model(production_id: int, repository: Repository) -> ProductionModelDto | Response:
    model = await repository.get_dto(production_id)
    return model if model is not None else problem(404, "Not Found")


@router.post("/models", status_code=201)
async def create_model(request: SaveProductionModelRequest, service: Service, response: Response) -> CreatedId:
    production_id = await service.create_model(request)
    response.headers["Location"] = f"/production/models/{production_id}"
    return CreatedId(id=production_id)


@router.put("/models/{production_id}", status_code=204, summary="Змінити модель і перелік розмірів")
async def update_model(production_id: int, request: SaveProductionModelRequest, service: Service) -> None:
    await service.update_model(production_id, request)


@router.delete("/models/{production_id}", status_code=204, summary="Приховати модель (status = 0)")
async def deactivate_model(production_id: int, service: Service) -> None:
    await service.deactivate_model(production_id)


@router.put("/models/{production_id}/links", status_code=204, summary="Прив'язати модель до товарів і розміри до опцій")
async def set_links(production_id: int, request: SetProductionLinksRequest, service: Service) -> None:
    await service.set_links(production_id, request)


@router.put("/models/{production_id}/sewn-bom", status_code=204, summary="Специфікація пошиву")
async def set_sewn_bom(production_id: int, request: SetSewnBomRequest, service: Service) -> None:
    await service.set_sewn_bom(production_id, request)


@router.post("/storefront/sync", summary="Передати пошиті залишки всіх активних моделей на вітрину")
async def storefront_sync(service: Service) -> StorefrontSyncResult:
    return await service.push_storefront()


def _operation(operation: ProductionOperation, summary: str) -> None:
    """The four stock operations take quantities for several sizes at once."""

    @router.post(f"/models/{{production_id}}/{operation.value}", summary=summary)
    async def record(
        production_id: int, request: ProductionOperationRequest, principal: CurrentPrincipal, service: Service
    ) -> ProductionOperationResult:
        return await service.record(production_id, operation, request, principal.user_id, principal.username)


_operation(ProductionOperation.CUT, "+Покроєно: крій зростає, розхідники списуються за специфікацією крою")
_operation(ProductionOperation.SEND_TO_SEWING, "В пошив: розкроєні комплекти передано швачкам")
_operation(ProductionOperation.SEWN, "+Пошито: пошиті вироби зростають, деталі й розхідники списуються")
_operation(ProductionOperation.SHIP, "Відвантажено не через замовлення")


@router.post("/models/{production_id}/stocktaking", summary="Інвентаризація моделі: крій, пошите й деталі")
async def stocktaking(
    production_id: int, request: ProductionStocktakingRequest, principal: CurrentPrincipal, service: Service
) -> ProductionStocktakingResult:
    return await service.recount(production_id, request, principal.user_id, principal.username)


@router.get("/by-warehouse", tags=["Виробництво: за складами"], summary="Пошиті вироби за місцями")
async def by_warehouse(service: ByWarehouse) -> SewnByWarehouseDto:
    return await service.by_warehouse()


@router.get("/models/{production_id}/places", tags=["Виробництво: за складами"], summary="Де лежать пошиті вироби")
async def places(production_id: int, service: ByWarehouse) -> list[SizePlacesDto]:
    return await service.get(production_id)


@router.post(
    "/models/{production_id}/transfers", tags=["Виробництво: за складами"], summary="Перемістити пошиті вироби"
)
async def transfer(
    production_id: int, request: TransferSewnRequest, principal: CurrentPrincipal, service: ByWarehouse
) -> list[SizePlacesDto]:
    return await service.transfer(production_id, request, principal.user_id, principal.username)


@router.get("/models/{production_id}/transfers", tags=["Виробництво: за складами"], summary="Журнал переміщень")
async def transfers(production_id: int, service: ByWarehouse) -> list[SewnTransferDto]:
    return await service.transfers(production_id)


@router.get("/plan", summary="План пошиву для активних замовлень; onlyToSew=false — також розміри, яких вистачає")
async def plan(
    service: Annotated[SewingPlanService, Depends(get_plan_service)],
    only_to_sew: Annotated[bool, Query(alias="onlyToSew")] = True,
) -> SewingPlanDto:
    return await service.get(only_to_sew)


@router.get("/log", summary="Журнал крою й пошиву (stage = cut або sewn), нові записи першими")
async def log(
    repository: Repository,
    production_id: Annotated[int | None, Query(alias="productionId")] = None,
    stage: str | None = None,
    limit: int = 100,
) -> list[ProductionLogDto]:
    return await repository.list_log(ProductionLogFilter(production_id, stage, limit))


@router.post("/models/{production_id}/components", status_code=201, summary="Додати деталь крою")
async def add_component(
    production_id: int, request: SaveComponentRequest, service: Service, response: Response
) -> CreatedId:
    component_id = await service.add_component(production_id, request)
    response.headers["Location"] = f"/production/components/{component_id}"
    return CreatedId(id=component_id)


@router.put("/components/{component_id}", status_code=204)
async def update_component(component_id: int, request: SaveComponentRequest, service: Service) -> None:
    await service.update_component(component_id, request)


@router.delete("/components/{component_id}", status_code=204)
async def delete_component(component_id: int, service: Service) -> None:
    await service.delete_component(component_id)


@router.put("/components/{component_id}/bom", status_code=204, summary="Специфікація крою: розхідники на одну деталь")
async def set_component_bom(component_id: int, request: SetComponentBomRequest, service: Service) -> None:
    await service.set_component_bom(component_id, request)
