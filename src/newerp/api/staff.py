from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from newerp.api.deps import Session
from newerp.api.security import Policies, require_roles
from newerp.application.staff import BonusReport, BonusService
from newerp.config import get_settings
from newerp.infrastructure.staff import SqlBonusDataSource

router = APIRouter(prefix="/staff", tags=["Персонал"])


def get_bonus_service(session: Session) -> BonusService:
    bonus = get_settings().bonus
    return BonusService(SqlBonusDataSource(session), bonus.percent, bonus.counted_status_ids)


@router.get(
    "/bonuses",
    dependencies=[Depends(require_roles(*Policies.BONUSES))],
    summary="Бонуси менеджерів за період (дати включно)",
)
async def bonuses(
    from_: Annotated[date, Query(alias="from")],
    to: date,
    service: Annotated[BonusService, Depends(get_bonus_service)],
) -> BonusReport:
    return await service.calculate(from_, to)
