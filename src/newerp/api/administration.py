"""Administration of the old admin (Python first, items 63–68), the admin only: the store settings, the OpenCart
user groups and API users, the error log, the database backup and the uploaded files."""

import re
import tempfile
from datetime import datetime
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import StreamingResponse

from newerp.api.deps import ClockDep, Session
from newerp.api.security import CurrentPrincipal, Policies, require_roles
from newerp.application.admin_files import (
    BackupService,
    ErrorLogDto,
    ErrorLogService,
    UploadDto,
    UploadService,
)
from newerp.application.module_settings import STORE_CONFIG, ModuleSettingsService, SettingGroupDto
from newerp.application.opencart_access import ApiKeyDto, ApiUserDto, ApiUserService, UserGroupDto, UserGroupService
from newerp.application.status_lists import StatusItemDto, StatusList, StatusListService
from newerp.config import get_settings
from newerp.infrastructure.admin_files import LocalLogFile, LocalUploadFolder, SqlBackupStore, SqlUploadStore
from newerp.infrastructure.module_settings import SqlModuleSettingsStore
from newerp.infrastructure.opencart_access import SqlApiUserStore, SqlUserGroupStore
from newerp.infrastructure.status_lists import SqlStatusListStore

router = APIRouter(
    prefix="/admin", tags=["Адміністрування"], dependencies=[Depends(require_roles(*Policies.ADMIN_TOOLS))]
)


def get_store_settings(session: Session) -> ModuleSettingsService:
    return ModuleSettingsService(SqlModuleSettingsStore(session), STORE_CONFIG)


def get_user_groups(session: Session) -> UserGroupService:
    return UserGroupService(SqlUserGroupStore(session))


def get_api_users(session: Session, clock: ClockDep) -> ApiUserService:
    return ApiUserService(SqlApiUserStore(session), clock)


StoreSettings = Annotated[ModuleSettingsService, Depends(get_store_settings)]
UserGroups = Annotated[UserGroupService, Depends(get_user_groups)]
ApiUsers = Annotated[ApiUserService, Depends(get_api_users)]


@router.get("/settings", summary="Налаштування магазину за групами; паролі й ключі — лише «задано/не задано»")
async def store_settings(service: StoreSettings) -> list[SettingGroupDto]:
    return await service.groups()


@router.put("/settings", summary="Змінити звичайні налаштування; секрети, коди й дозволи файлів — лише в OpenCart")
async def save_store_settings(
    changes: dict[str, str], principal: CurrentPrincipal, service: StoreSettings
) -> list[SettingGroupDto]:
    return await service.save(changes, principal.user_id)


@router.get("/user-groups", summary="Групи користувачів старої адмінки з дозволами сторінок")
async def list_user_groups(service: UserGroups) -> list[UserGroupDto]:
    return await service.groups()


@router.get("/user-groups/routes", summary="Сторінки адмінки, що трапляються в дозволах груп")
async def known_routes(service: UserGroups) -> list[str]:
    return await service.known_routes()


@router.post("/user-groups", status_code=201, summary="Нова група")
async def create_user_group(request: UserGroupDto, principal: CurrentPrincipal, service: UserGroups) -> UserGroupDto:
    return await service.save(None, request, principal.user_id)


@router.put("/user-groups/{group_id}", summary="Змінити групу (свою групу не можна позбавити сторінки дозволів)")
async def update_user_group(
    group_id: int, request: UserGroupDto, principal: CurrentPrincipal, service: UserGroups
) -> UserGroupDto:
    return await service.save(group_id, request, principal.user_id)


@router.delete("/user-groups/{group_id}", status_code=204, summary="Видалити групу без користувачів")
async def delete_user_group(group_id: int, principal: CurrentPrincipal, service: UserGroups) -> None:
    await service.delete(group_id, principal.user_id)


@router.get("/api-users", summary="Користувачі API OpenCart: IP, сесії, чи задано ключ (сам ключ не видається)")
async def list_api_users(service: ApiUsers) -> list[ApiUserDto]:
    return await service.users()


@router.post("/api-users", status_code=201, summary="Новий користувач API (ключ — окремою дією)")
async def create_api_user(request: ApiUserDto, principal: CurrentPrincipal, service: ApiUsers) -> ApiUserDto:
    return await service.save(None, request, principal.user_id)


@router.put("/api-users/{api_id}", summary="Змінити ім'я, увімкнення, дозволені IP")
async def update_api_user(
    api_id: int, request: ApiUserDto, principal: CurrentPrincipal, service: ApiUsers
) -> ApiUserDto:
    return await service.save(api_id, request, principal.user_id)


@router.post("/api-users/{api_id}/key", summary="Новий ключ замість старого; показується лише цей один раз")
async def new_api_key(api_id: int, principal: CurrentPrincipal, service: ApiUsers) -> ApiKeyDto:
    return await service.new_key(api_id, principal.user_id)


@router.delete("/api-users/{api_id}", status_code=204, summary="Видалити користувача API з його IP і сесіями")
async def delete_api_user(api_id: int, principal: CurrentPrincipal, service: ApiUsers) -> None:
    await service.delete(api_id, principal.user_id)


# ═══ Error log, backup, uploaded files (items 66–68) ═══


def get_error_log() -> ErrorLogService:
    return ErrorLogService(LocalLogFile(get_settings().admin_tools.error_log))


def get_backup(session: Session) -> BackupService:
    return BackupService(SqlBackupStore(session))


def get_uploads(session: Session) -> UploadService:
    return UploadService(SqlUploadStore(session), LocalUploadFolder(get_settings().admin_tools.upload_dir))


ErrorLog = Annotated[ErrorLogService, Depends(get_error_log)]
Backup = Annotated[BackupService, Depends(get_backup)]
Uploads = Annotated[UploadService, Depends(get_uploads)]


@router.get("/error-log", summary="Кінець журналу помилок OpenCart (паролі й токени приховано)")
async def error_log(service: ErrorLog) -> ErrorLogDto:
    return service.view()


@router.get("/error-log/download", response_class=Response, summary="Журнал помилок файлом (до 5 МБ з кінця)")
async def download_error_log(principal: CurrentPrincipal, service: ErrorLog) -> Response:
    return Response(
        service.download(principal.user_id), media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="error.log"'},
    )  # fmt: skip


@router.delete("/error-log", status_code=204, summary="Очистити журнал помилок")
async def clear_error_log(principal: CurrentPrincipal, service: ErrorLog) -> None:
    service.clear(principal.user_id)


@router.get("/backup/tables", summary="Таблиці магазину, які можна вивантажити")
async def backup_tables(service: Backup) -> list[str]:
    return await service.tables()


@router.get(
    "/backup.sql.gz", response_class=Response,
    summary="Резервна копія таблиць (усіх або ?tables=…) як SQL у gzip; відновлення — лише з командного рядка",
)  # fmt: skip
async def backup(
    principal: CurrentPrincipal, service: Backup, tables: Annotated[list[str] | None, Query()] = None
) -> StreamingResponse:
    # Up to 20 MB in memory, then a temporary file; streamed to the browser once complete.
    spool = tempfile.SpooledTemporaryFile(max_size=20_000_000)
    await service.dump(tables, spool, principal.user_id)
    spool.seek(0)
    name = f"newerp-backup-{datetime.now():%Y%m%d-%H%M}.sql.gz"
    return StreamingResponse(
        _chunks(spool), media_type="application/gzip", headers={"Content-Disposition": f'attachment; filename="{name}"'}
    )


def _chunks(file, size: int = 1_000_000):
    try:
        while chunk := file.read(size):
            yield chunk
    finally:
        file.close()


@router.get("/uploads", summary="Файли, що покупці завантажили на вітрині")
async def list_uploads(service: Uploads) -> list[UploadDto]:
    return await service.uploads()


@router.get("/uploads/{upload_id}/file", response_class=Response, summary="Завантажити файл покупця")
async def download_upload(upload_id: int, principal: CurrentPrincipal, service: Uploads) -> Response:
    name, content = await service.download(upload_id, principal.user_id)
    # A header is Latin-1: an ASCII name for old browsers and the real one encoded as RFC 5987 describes.
    ascii_name = re.sub(r"[^A-Za-z0-9._-]", "_", name)[:100] or "file"
    disposition = f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name[:100])}"
    return Response(
        content, media_type="application/octet-stream",
        headers={"Content-Disposition": disposition, "X-Content-Type-Options": "nosniff"},
    )  # fmt: skip


@router.delete("/uploads/{upload_id}", status_code=204, summary="Видалити файл і його запис")
async def delete_upload(upload_id: int, principal: CurrentPrincipal, service: Uploads) -> None:
    await service.delete(upload_id, principal.user_id)


# ═══ Status lists (item 69) ═══


def get_status_lists(session: Session) -> StatusListService:
    return StatusListService(SqlStatusListStore(session, get_settings().language_id))


StatusLists = Annotated[StatusListService, Depends(get_status_lists)]


@router.get("/status-lists/{kind}", summary="Статуси замовлень (з кольором), повернень, причини, дії, наявності")
async def list_statuses(kind: StatusList, service: StatusLists) -> list[StatusItemDto]:
    return await service.items(kind)


@router.post("/status-lists/{kind}", status_code=201, summary="Новий запис довідника")
async def create_status(
    kind: StatusList, request: StatusItemDto, principal: CurrentPrincipal, service: StatusLists
) -> StatusItemDto:
    return await service.save(kind, None, request, principal.user_id)


@router.put("/status-lists/{kind}/{item_id}", summary="Перейменувати (замовлення й повернення бачать нову назву)")
async def update_status(
    kind: StatusList, item_id: int, request: StatusItemDto, principal: CurrentPrincipal, service: StatusLists
) -> StatusItemDto:
    return await service.save(kind, item_id, request, principal.user_id)


@router.delete("/status-lists/{kind}/{item_id}", status_code=204, summary="Видалити запис, якого ніщо не має")
async def delete_status(kind: StatusList, item_id: int, principal: CurrentPrincipal, service: StatusLists) -> None:
    await service.delete(kind, item_id, principal.user_id)
