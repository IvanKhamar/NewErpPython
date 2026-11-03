"""The old admin's tools (Python first, items 66–68): the error log, the database backup, the uploaded files.

What each one guards against:
- the log is shown with passwords, tokens and keys masked, and only its last part;
- the backup is for the admin only, of the shop's own tables only (dd_, erp_), and every download is logged; a restore
  stays a command-line step, as a mistake there cannot be undone from a web page;
- an uploaded file is found by its database record, and its path must stay inside the upload folder."""

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import BinaryIO, Protocol

from newerp.application.common import Dto, NotFoundError
from newerp.domain.common import DomainError

audit = logging.getLogger("newerp.audit")
LOG_TAIL_BYTES = 64_000
LOG_DOWNLOAD_BYTES = 5_000_000
_SECRET = re.compile(
    r"(?i)\b(password|passwd|pwd|token|api[_-]?key|key|secret|authorization|x-token)(\s*[=:]\s*|\s+)(['\"]?)"
    r"([^\s&'\",;]+)"
)
TABLE = re.compile(r"(dd|erp)_[a-z0-9_]{1,60}")


def mask_secrets(text: str) -> str:
    """ "password=abc" → "password=***", as OpenCart writes failed SQL and request data into its log."""
    return _SECRET.sub(lambda m: f"{m[1]}{m[2]}{m[3]}***", text)


# ── 66: the error log ──


class LogFile(Protocol):
    @property
    def enabled(self) -> bool: ...

    def tail(self, size: int) -> tuple[str, int]:
        """The last size bytes as text and the whole file's size."""
        ...

    def clear(self) -> None: ...


class ErrorLogDto(Dto):
    enabled: bool
    size: int
    text: str  # masked, the last part only
    cut: bool  # the file is longer than what is shown


class ErrorLogService:
    def __init__(self, log: LogFile) -> None:
        self._log = log

    def view(self) -> ErrorLogDto:
        if not self._log.enabled:
            return ErrorLogDto(enabled=False, size=0, text="", cut=False)
        text, size = self._log.tail(LOG_TAIL_BYTES)
        return ErrorLogDto(enabled=True, size=size, text=mask_secrets(text), cut=size > LOG_TAIL_BYTES)

    def download(self, user_id: int) -> str:
        self._require()
        text, _ = self._log.tail(LOG_DOWNLOAD_BYTES)
        audit.info("error log downloaded, by user %s", user_id)
        return mask_secrets(text)

    def clear(self, user_id: int) -> None:
        self._require()
        self._log.clear()
        audit.warning("error log cleared, by user %s", user_id)

    def _require(self) -> None:
        if not self._log.enabled:
            raise DomainError("Журнал помилок не підключено (NEWERP_ADMIN_TOOLS__ERROR_LOG).")


# ── 67: the backup ──


class BackupStore(Protocol):
    async def tables(self) -> list[str]:
        """The shop's own tables (dd_…, erp_…) of the database."""
        ...

    async def dump(self, tables: list[str], out: BinaryIO) -> None:
        """Writes CREATE TABLE and INSERT statements of these tables, gzip-compressed."""
        ...


class BackupService:
    def __init__(self, store: BackupStore) -> None:
        self._store = store

    async def tables(self) -> list[str]:
        return await self._store.tables()

    async def dump(self, tables: list[str] | None, out: BinaryIO, user_id: int) -> list[str]:
        """All the shop's tables, or the chosen ones (each must be one of them)."""
        known = await self._store.tables()
        chosen = sorted(set(tables)) if tables else known
        unknown = [t for t in chosen if t not in known or not TABLE.fullmatch(t)]
        if unknown:
            raise DomainError(f"Таблиць немає або їх не можна вивантажити: {', '.join(unknown[:5])}.")
        await self._store.dump(chosen, out)
        audit.warning("database backup downloaded: %s tables, by user %s", len(chosen), user_id)
        return chosen


# ── 68: uploaded files ──


@dataclass(frozen=True)
class UploadRow:
    id: int
    name: str  # what the customer called it
    filename: str  # the stored file: name.<random>
    code: str
    date_added: datetime | None


class UploadDto(Dto):
    id: int
    name: str
    code: str
    date_added: datetime | None
    size: int | None  # None: the file is missing or the folder is not connected


class UploadStore(Protocol):
    async def uploads(self) -> list[UploadRow]: ...

    async def find(self, upload_id: int) -> UploadRow | None: ...

    async def delete(self, upload_id: int) -> None: ...


class UploadFolder(Protocol):
    @property
    def enabled(self) -> bool: ...

    def size(self, filename: str) -> int | None: ...

    def read(self, filename: str) -> bytes: ...

    def remove(self, filename: str) -> None: ...


class UploadService:
    def __init__(self, store: UploadStore, folder: UploadFolder) -> None:
        self._store = store
        self._folder = folder

    async def uploads(self) -> list[UploadDto]:
        return [
            UploadDto(
                id=u.id,
                name=u.name,
                code=u.code,
                date_added=u.date_added,
                size=self._folder.size(u.filename) if self._folder.enabled else None,
            )  # fmt: skip
            for u in await self._store.uploads()
        ]

    async def download(self, upload_id: int, user_id: int) -> tuple[str, bytes]:
        upload = await self._find(upload_id)
        if not self._folder.enabled:
            raise DomainError("Папку завантажень не підключено (NEWERP_ADMIN_TOOLS__UPLOAD_DIR).")
        content = self._folder.read(upload.filename)
        audit.info("uploaded file downloaded: %s, by user %s", upload_id, user_id)
        return upload.name, content

    async def delete(self, upload_id: int, user_id: int) -> None:
        upload = await self._find(upload_id)
        if self._folder.enabled:
            self._folder.remove(upload.filename)
        await self._store.delete(upload_id)
        audit.info("uploaded file deleted: %s, by user %s", upload_id, user_id)

    async def _find(self, upload_id: int) -> UploadRow:
        upload = await self._store.find(upload_id)
        if upload is None:
            raise NotFoundError(f"Файлу {upload_id} немає.")
        return upload
