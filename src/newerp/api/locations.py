from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from newerp.api.deps import Session
from newerp.api.files import read_upload, xlsx_file
from newerp.api.security import Policies, require_roles
from newerp.application.common import CreatedId
from newerp.application.locations import (
    MAX_IMPORT_BYTES,
    LocationDto,
    LocationImportResult,
    LocationService,
    SaveLocationRequest,
)
from newerp.infrastructure.locations import SqlLocationStore
from newerp.infrastructure.spreadsheets import OpenpyxlSpreadsheet
from newerp.infrastructure.stock.repositories import SqlStockLocationRepository

router = APIRouter(
    prefix="/locations", tags=["Склади й пункти видачі"], dependencies=[Depends(require_roles(*Policies.ORDERS))]
)


def get_location_service(session: Session) -> LocationService:
    # Sewn pieces at warehouses (production) are added to the holders when that module is ported.
    holders = [SqlStockLocationRepository(session)]
    return LocationService(SqlLocationStore(session), OpenpyxlSpreadsheet(), holders)


Service = Annotated[LocationService, Depends(get_location_service)]


@router.get("", summary="Довідник складів і пунктів видачі; search — назва, місто, адреса, тип")
async def list_locations(service: Service, search: str | None = None) -> list[LocationDto]:
    return await service.list_all(search)


@router.get("/export.xlsx", summary="Вивантажити довідник у Excel (колонки як у старого сайту)")
async def export_locations(service: Service) -> Response:
    return xlsx_file(await service.export(), "sklady")


@router.post("/import", summary="Імпорт з Excel (сире тіло запиту); з помилками нічого не записується")
async def import_locations(request: Request, service: Service) -> LocationImportResult:
    return await service.import_file(await read_upload(request, MAX_IMPORT_BYTES))


@router.get("/{location_id}", summary="Склад чи пункт видачі")
async def get_location(location_id: int, service: Service) -> LocationDto:
    return await service.get(location_id)


@router.post("", status_code=201, summary="Новий склад чи пункт видачі")
async def create_location(request: SaveLocationRequest, service: Service, response: Response) -> CreatedId:
    location_id = await service.create(request)
    response.headers["Location"] = f"/locations/{location_id}"
    return CreatedId(id=location_id)


@router.put("/{location_id}", status_code=204, summary="Змінити склад чи пункт видачі")
async def update_location(location_id: int, request: SaveLocationRequest, service: Service) -> None:
    await service.update(location_id, request)


@router.delete("/{location_id}", status_code=204, summary="Видалити склад чи пункт видачі")
async def delete_location(location_id: int, service: Service) -> None:
    await service.delete(location_id)
