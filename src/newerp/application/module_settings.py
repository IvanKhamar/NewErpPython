"""Editing a storefront module's settings (dd_setting rows of one code), used for the remarketing module (item 62).

The ERP must not become a way to attack the storefront or to read its keys, so every key is one of:
- secret: tokens, passwords, API keys — shown only as "set" or "not set", never sent to the browser, never changed;
- locked: what the storefront runs as code (JavaScript counters, SQL) — shown, but changed only in OpenCart;
- list: arrays saved as JSON — shown, changed in OpenCart;
- text: everything else — editable, as plain text up to MAX_VALUE characters, without line breaks."""

import logging
import re
from dataclasses import dataclass
from typing import Literal, Protocol

from newerp.application.common import Dto
from newerp.domain.common import DomainError

audit = logging.getLogger("newerp.audit")
MAX_VALUE = 1000
SettingKind = Literal["text", "secret", "locked", "list"]


@dataclass(frozen=True)
class SettingRow:
    key: str
    value: str
    serialized: bool


@dataclass(frozen=True)
class SettingRules:
    code: str
    prefix: str  # "remarketing_": cut off to make the group names
    secret: re.Pattern
    locked: re.Pattern


class SettingDto(Dto):
    key: str
    kind: SettingKind
    value: str | None  # None for a secret
    is_set: bool


class SettingGroupDto(Dto):
    name: str  # "facebook", "ga4", "feed"…
    settings: list[SettingDto]


class ModuleSettingsStore(Protocol):
    async def rows(self, code: str) -> list[SettingRow]: ...

    async def update(self, code: str, values: dict[str, str]) -> None:
        """Only these keys' values; nothing is added or removed."""
        ...


class ModuleSettingsService:
    def __init__(self, store: ModuleSettingsStore, rules: SettingRules) -> None:
        self._store = store
        self._rules = rules

    async def groups(self) -> list[SettingGroupDto]:
        groups: dict[str, list[SettingDto]] = {}
        for row in sorted(await self._store.rows(self._rules.code), key=lambda r: r.key):
            kind = self._kind(row)
            short = row.key.removeprefix(self._rules.prefix)
            name = short.split("_", 1)[0] if "_" in short else "general"
            groups.setdefault(name, []).append(
                SettingDto(
                    key=row.key,
                    kind=kind,
                    value=None if kind == "secret" else row.value,
                    is_set=bool(row.value.strip()),
                )  # fmt: skip
            )
        return [SettingGroupDto(name=name, settings=items) for name, items in sorted(groups.items())]

    async def save(self, changes: dict[str, str], user_id: int) -> list[SettingGroupDto]:
        rows = {row.key: row for row in await self._store.rows(self._rules.code)}
        clean: dict[str, str] = {}
        for key, value in changes.items():
            row = rows.get(key)
            if row is None:
                raise DomainError(f"Налаштування «{key}» у модулі немає.")
            kind = self._kind(row)
            if kind != "text":
                raise DomainError(f"«{key}» не змінюється тут ({'секрет' if kind == 'secret' else 'лише в OpenCart'}).")
            text = value.strip()
            if len(text) > MAX_VALUE or "\n" in text or "\r" in text:
                raise DomainError(f"«{key}»: один рядок до {MAX_VALUE} знаків.")
            if text != row.value:
                clean[key] = text
        if clean:
            await self._store.update(self._rules.code, clean)
            audit.info("%s settings changed: %s, by user %s", self._rules.code, ", ".join(sorted(clean)), user_id)
        return await self.groups()

    def _kind(self, row: SettingRow) -> SettingKind:
        if self._rules.secret.search(row.key):
            return "secret"
        if self._rules.locked.search(row.key):
            return "locked"
        if row.serialized:
            return "list"
        return "text"


REMARKETING = SettingRules(
    code="remarketing",
    prefix="remarketing_",
    # Facebook and TikTok tokens, eSputnik password, GA4 API secrets, the Telegram bot (its id carries the token).
    secret=re.compile(r"(token|password|secret|_key$|measurement_key|bot_id|_login$)"),
    # Counters are raw JavaScript put on every page; custom SQL runs on the storefront's database.
    locked=re.compile(r"(counter|custom_sql|_route$)"),
)
