"""Module settings rows of dd_setting (store 0)."""

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.application.module_settings import SettingRow
from newerp.infrastructure.models.settings import DdSetting

STORE_ID = 0


class SqlModuleSettingsStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def rows(self, code: str) -> list[SettingRow]:
        s = DdSetting
        rows = await self._session.execute(
            select(s.key, s.value, s.serialized).where(s.store_id == STORE_ID, s.code == code)
        )
        return [SettingRow(key, value or "", bool(serialized)) for key, value, serialized in rows]

    async def update(self, code: str, values: dict[str, str]) -> None:
        s = DdSetting
        for key, value in values.items():
            await self._session.execute(
                update(s).where(s.store_id == STORE_ID, s.code == code, s.key == key).values(value=value)
            )
        await self._session.commit()
