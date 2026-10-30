from typing import Annotated

from fastapi import APIRouter, Depends, Response

from newerp.api.deps import get_user_admin_service
from newerp.api.security import CurrentPrincipal, Policies, require_roles
from newerp.application.auth.models import (
    CreateUserRequest,
    ResetPasswordRequest,
    SetAccessRequest,
    UserAccessResult,
    UserDto,
)
from newerp.application.auth.services import UserAdminService

router = APIRouter(prefix="/users", tags=["Користувачі"], dependencies=[Depends(require_roles(*Policies.USERS))])
Service = Annotated[UserAdminService, Depends(get_user_admin_service)]


@router.get("", summary="Усі користувачі старого сайту: чи є доступ до NewErp, роль, останній вхід")
async def list_users(service: Service) -> list[UserDto]:
    return await service.list_all()


@router.post("", status_code=201, summary="Новий працівник з тимчасовим паролем")
async def create_user(request: CreateUserRequest, service: Service, response: Response) -> UserAccessResult:
    result = await service.create(request)
    response.headers["Location"] = f"/users/{result.user_id}"
    return result


@router.put("/{user_id}/access", summary="Видати доступ або змінити роль (Admin, Manager, Storekeeper)")
async def set_access(
    user_id: int, request: SetAccessRequest, principal: CurrentPrincipal, service: Service
) -> UserAccessResult:
    return await service.set_access(principal.user_id, user_id, request)


@router.delete("/{user_id}/access", status_code=204, summary="Забрати доступ до NewErp")
async def revoke_access(user_id: int, principal: CurrentPrincipal, service: Service) -> None:
    await service.revoke(principal.user_id, user_id)


@router.post("/{user_id}/reset-password", summary="Скинути пароль на тимчасовий (повертається один раз)")
async def reset_password(user_id: int, request: ResetPasswordRequest, service: Service) -> UserAccessResult:
    return await service.reset_password(user_id, request)
