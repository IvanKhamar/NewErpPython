"""The error log file, the SQL backup writer and the uploads folder.

The backup writes what OpenCart's tool/backup wrote (CREATE TABLE and INSERT statements), table by table and
in batches, straight into a gzip stream, so even a large table is never held in memory whole."""

import gzip
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.application.admin_files import TABLE, UploadRow
from newerp.domain.common import DomainError

BATCH = 500

metadata = MetaData()
upload = Table(
    "dd_upload",
    metadata,
    Column("upload_id", Integer, primary_key=True),
    Column("name", String(255)),
    Column("filename", String(255)),
    Column("code", String(255)),
    Column("date_added", DateTime),
)


# ── The error log ──


class LocalLogFile:
    def __init__(self, path: str) -> None:
        self._path = Path(path) if path.strip() else None

    @property
    def enabled(self) -> bool:
        return self._path is not None and self._path.is_file()

    def tail(self, size: int) -> tuple[str, int]:
        assert self._path is not None
        total = self._path.stat().st_size
        with self._path.open("rb") as file:
            file.seek(max(0, total - size))
            return file.read().decode("utf-8", errors="replace"), total

    def clear(self) -> None:
        assert self._path is not None
        self._path.write_bytes(b"")


# ── The backup ──


def sql_literal(value: object) -> str:
    """A value as MySQL reads it back: NULL, a number, X'…' for bytes, or a quoted string with \\ ' and control
    characters escaped (the old site's zero dates come as text and stay as they were)."""
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int | float | Decimal):
        return str(value)
    if isinstance(value, bytes | bytearray):
        return "X'" + bytes(value).hex() + "'" if value else "''"
    if isinstance(value, datetime):
        value = value.strftime("%Y-%m-%d %H:%M:%S")
    elif isinstance(value, date | time):
        value = value.isoformat()
    elif isinstance(value, timedelta):
        seconds = int(value.total_seconds())
        value = f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
    escaped = (
        str(value).replace("\\", "\\\\").replace("'", "\\'").replace("\0", "\\0").replace("\n", "\\n")
        .replace("\r", "\\r").replace("\x1a", "\\Z")
    )  # fmt: skip
    return f"'{escaped}'"


class SqlBackupStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def tables(self) -> list[str]:
        rows = await self._session.execute(
            text(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = DATABASE() "
                "AND table_type = 'BASE TABLE' ORDER BY table_name"
            )
        )
        return [name for (name,) in rows if TABLE.fullmatch(name)]

    async def dump(self, tables: list[str], out: BinaryIO) -> None:
        connection = await self._session.connection()
        with gzip.GzipFile(fileobj=out, mode="wb") as archive:
            archive.write(b"-- NewErp backup\nSET NAMES utf8mb4;\nSET FOREIGN_KEY_CHECKS = 0;\n\n")
            for table in tables:
                # The name was checked against the database's own list; a bound parameter cannot name a table.
                if not TABLE.fullmatch(table):
                    raise DomainError(f"Недозволена назва таблиці: {table}.")
                create = (await connection.exec_driver_sql(f"SHOW CREATE TABLE `{table}`")).one()[1]
                archive.write(f"DROP TABLE IF EXISTS `{table}`;\n{create};\n\n".encode())
                result = await connection.exec_driver_sql(f"SELECT * FROM `{table}`")
                columns = ", ".join(f"`{c}`" for c in result.keys())
                rows = result.fetchmany(BATCH)
                while rows:
                    values = ",\n".join("(" + ", ".join(sql_literal(v) for v in row) + ")" for row in rows)
                    archive.write(f"INSERT INTO `{table}` ({columns}) VALUES\n{values};\n".encode())
                    rows = result.fetchmany(BATCH)
                archive.write(b"\n")


# ── Uploaded files ──


class SqlUploadStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def uploads(self) -> list[UploadRow]:
        u = upload.c
        rows = await self._session.execute(
            select(u.upload_id, u.name, u.filename, u.code, u.date_added).order_by(u.upload_id.desc())
        )
        return [UploadRow(*row) for row in rows]

    async def find(self, upload_id: int) -> UploadRow | None:
        u = upload.c
        row = (
            await self._session.execute(
                select(u.upload_id, u.name, u.filename, u.code, u.date_added).where(u.upload_id == upload_id)
            )
        ).first()
        return None if row is None else UploadRow(*row)

    async def delete(self, upload_id: int) -> None:
        await self._session.execute(delete(upload).where(upload.c.upload_id == upload_id))
        await self._session.commit()


class LocalUploadFolder:
    """OpenCart's storage/upload. A stored name that would lead outside the folder (../, a full path) is refused."""

    def __init__(self, path: str) -> None:
        self._root = Path(path).resolve() if path.strip() else None

    @property
    def enabled(self) -> bool:
        return self._root is not None and self._root.is_dir()

    def _path(self, filename: str) -> Path:
        assert self._root is not None
        path = (self._root / filename).resolve()
        if path.parent != self._root:
            raise DomainError("Шлях файлу виходить за папку завантажень.")
        return path

    def size(self, filename: str) -> int | None:
        try:
            path = self._path(filename)
        except DomainError:
            return None
        return path.stat().st_size if path.is_file() else None

    def read(self, filename: str) -> bytes:
        path = self._path(filename)
        if not path.is_file():
            raise DomainError("Файлу на диску немає.")
        return path.read_bytes()

    def remove(self, filename: str) -> None:
        path = self._path(filename)
        if path.is_file():
            path.unlink()
