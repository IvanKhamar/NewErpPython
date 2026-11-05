"""Klaviyo, the TikTok feed and Instagram over the OpenCart tables, and the Instagram Graph API client."""

import logging
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from sqlalchemy import BigInteger, Column, DateTime, MetaData, String, Table, Text, delete, func, insert, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.application.marketing.integrations import (
    INSTAGRAM_HOSTS,
    FeedProduct,
    InstagramMedia,
    InstagramToken,
    KlaviyoContact,
    KlaviyoList,
    KlaviyoSettingsDto,
    mask_error,
)
from newerp.config import TikTokSyncSettings
from newerp.domain.common import DomainError
from newerp.infrastructure.catalog.product_edit import STORE_ID, seo_url
from newerp.infrastructure.models.catalog import DdProduct, DdProductDescription
from newerp.infrastructure.models.customers import DdCustomer
from newerp.infrastructure.models.settings import DdSetting

TIMEOUT_SECONDS = 30
MAX_PICTURE_BYTES = 5_000_000
GRAPH = "https://graph.instagram.com"
TIKTOK_API = "https://business-api.tiktok.com/open_api/v1.3"
TIKTOK_TIMEOUT_SECONDS = 120  # a batch of a thousand products

log = logging.getLogger(__name__)
_PICTURE_NAME = re.compile(r"\d{1,30}\.jpg")

metadata = MetaData()
instagram = Table(
    "dd_instagram",
    metadata,
    Column("instagram_id", BigInteger, primary_key=True),
    Column("media_type", String(32)),
    Column("media_url", Text),
    Column("caption", Text),
    Column("thumbnail_url", Text),
    Column("permalink", String(255)),
    Column("date_added", DateTime),
)


async def _settings(session: AsyncSession, code: str) -> dict[str, str]:
    rows = await session.execute(
        select(DdSetting.key, DdSetting.value).where(DdSetting.store_id == STORE_ID, DdSetting.code == code)
    )
    return {key: value for key, value in rows}


async def _put_settings(session: AsyncSession, code: str, values: dict[str, str]) -> None:
    """Replaces only these keys of the module; its other keys stay."""
    await session.execute(
        delete(DdSetting).where(DdSetting.store_id == STORE_ID, DdSetting.code == code, DdSetting.key.in_(values))
    )
    await session.execute(
        insert(DdSetting),
        [{"store_id": STORE_ID, "code": code, "key": k, "value": v, "serialized": False} for k, v in values.items()],
    )
    await session.commit()


class SqlKlaviyoStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def contacts(self, which: KlaviyoList) -> list[KlaviyoContact]:
        c = DdCustomer
        query = select(c.email, c.firstname, c.lastname, c.telephone).where(c.status.is_(True))
        if which != "customers":
            query = query.where(c.newsletter.is_(which == "subscribers"))
        rows = await self._session.execute(query.order_by(c.date_added.desc()))
        return [KlaviyoContact(*row) for row in rows]

    async def settings(self) -> KlaviyoSettingsDto:
        s = await _settings(self._session, "module_klaviyo")
        return KlaviyoSettingsDto(
            enabled=s.get("module_klaviyo_status") == "1",
            tracking=s.get("module_klaviyo_enable_tracking") == "1",
            newsletter_list_id=s.get("module_klaviyo_newsletter_list_id", ""),
        )

    async def save_settings(self, settings: KlaviyoSettingsDto) -> None:
        await _put_settings(self._session, "module_klaviyo", {
            "module_klaviyo_status": "1" if settings.enabled else "0",
            "module_klaviyo_enable_tracking": "1" if settings.tracking else "0",
            "module_klaviyo_newsletter_list_id": settings.newsletter_list_id,
        })  # fmt: skip


class SqlFeedStore:
    def __init__(self, session: AsyncSession, language_id: int) -> None:
        self._session = session
        self._language_id = language_id

    async def products(self, include_disabled: bool = False) -> list[FeedProduct]:
        p, d = DdProduct, DdProductDescription
        keyword = (
            select(seo_url.c.keyword)
            .where(seo_url.c.query == func.concat("product_id=", p.product_id), seo_url.c.store_id == STORE_ID)
            .limit(1)
            .scalar_subquery()
        )
        rows = await self._session.execute(
            select(p.product_id, d.name, d.description, p.price, p.quantity, p.image, keyword, p.status)
            .join(d, (d.product_id == p.product_id) & (d.language_id == self._language_id))
            .where(p.price > 0, p.image.is_not(None), p.image != "")
            .where(true() if include_disabled else p.status.is_(True))
            .order_by(p.product_id)
        )
        return [
            FeedProduct(
                product_id=r[0],
                name=r[1],
                description=r[2] or "",
                price=r[3],
                quantity=r[4],
                image=r[5],
                seo_keyword=r[6] or "",
                enabled=bool(r[7]),
            )  # fmt: skip
            for r in rows
        ]


class SqlInstagramStore:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def token(self) -> InstagramToken | None:
        s = await _settings(self._session, "module_instagram")
        value = s.get("module_instagram_token", "").strip()
        return InstagramToken(value, _old_date(s.get("module_instagram_token_date", ""))) if value else None

    async def save_token(self, token: str, now: datetime) -> None:
        values = {"module_instagram_token": token, "module_instagram_token_date": now.strftime("%Y-%m-%d %H:%M:%S")}
        await _put_settings(self._session, "module_instagram", values)

    async def known_ids(self) -> set[str]:
        return {str(i) for i in await self._session.scalars(select(instagram.c.instagram_id))}

    async def add(self, media: InstagramMedia) -> None:
        await self._session.execute(
            insert(instagram).values(
                instagram_id=int(media.id),
                media_type=media.media_type,
                media_url=media.media_url,
                caption=media.caption,
                thumbnail_url=media.thumbnail_url,
                permalink=media.permalink[:255],
                date_added=media.timestamp or datetime.now().replace(microsecond=0),
            )  # fmt: skip
        )
        await self._session.commit()


def _old_date(value: str) -> datetime | None:
    """The old module wrote date('Y:m:d h:m:s') (colons, and the month for the minutes); the date part is enough."""
    match = re.match(r"(\d{4})[:-](\d{2})[:-](\d{2})", value.strip())
    return datetime(int(match[1]), int(match[2]), int(match[3])) if match else None


class HttpTikTokCatalog:
    """TikTok's Business API, as the old module called it. The token goes only in the Access-Token header."""

    def __init__(self, settings: TikTokSyncSettings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._transport = transport

    @property
    def enabled(self) -> bool:
        return self._settings.enabled

    async def upload(self, products: list[dict]) -> bool:
        body = {"bc_id": self._settings.bc_id, "catalog_id": self._settings.catalog_id, "products": products}
        headers = {"Access-Token": self._settings.access_token.get_secret_value()}
        try:
            async with httpx.AsyncClient(timeout=TIKTOK_TIMEOUT_SECONDS, transport=self._transport,
                                         follow_redirects=False) as http:  # fmt: skip
                response = await http.post(f"{TIKTOK_API}/catalog/product/upload/", json=body, headers=headers)
            answer = response.json()
        except (httpx.HTTPError, ValueError) as error:
            log.warning("TikTok catalog batch failed: %s", mask_error(error))
            return False
        if response.status_code == 200 and answer.get("message") == "OK":
            return True
        log.warning("TikTok refused a catalog batch: HTTP %s, code %s", response.status_code, answer.get("code"))
        return False


class HttpInstagramClient:
    """Instagram's Graph API wants the token in the query; the URLs with it are never logged."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=TIMEOUT_SECONDS, transport=self._transport, follow_redirects=False)

    async def refresh(self, token: str) -> str:
        params = {"grant_type": "ig_refresh_token", "access_token": token}
        async with self._http() as http:
            response = await http.get(f"{GRAPH}/refresh_access_token", params=params)
        data = _json(response)
        if not data.get("access_token"):
            raise DomainError("Instagram не оновив токен: потрібен новий у налаштуваннях модуля вітрини.")
        return str(data["access_token"])

    async def media(self, token: str) -> list[InstagramMedia]:
        fields = "id,media_type,media_url,caption,timestamp,thumbnail_url,permalink"
        async with self._http() as http:
            response = await http.get(f"{GRAPH}/me/media", params={"fields": fields, "access_token": token})
        items = _json(response).get("data") or []
        return [
            InstagramMedia(
                id=str(i.get("id", "")),
                media_type=str(i.get("media_type", "")),
                media_url=str(i.get("media_url", "")),
                thumbnail_url=str(i.get("thumbnail_url", "")),
                caption=str(i.get("caption", "")),
                permalink=str(i.get("permalink", "")),
                timestamp=_timestamp(i.get("timestamp")),
            )  # fmt: skip
            for i in items
            if isinstance(i, dict)
        ]

    async def download(self, url: str) -> bytes:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if parts.scheme != "https" or not host.endswith(INSTAGRAM_HOSTS):
            raise DomainError("Картинку Instagram дозволено брати лише з його CDN через https.")
        async with self._http() as http, http.stream("GET", url) as response:
            if not response.is_success:
                raise DomainError(f"Instagram не віддав картинку ({response.status_code}).")
            content = b""
            async for chunk in response.aiter_bytes():
                content += chunk
                if len(content) > MAX_PICTURE_BYTES:
                    raise DomainError("Картинка Instagram більша за 5 МБ.")
        return content


def _json(response: httpx.Response) -> dict:
    try:
        data = response.json()
    except ValueError:
        data = {}
    if not response.is_success or not isinstance(data, dict):
        message = (data.get("error") or {}).get("message") if isinstance(data, dict) else None
        raise DomainError(f"Instagram відмовив ({response.status_code}): {message or 'невідома помилка'}.")
    return data


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.replace("+0000", "+00:00"))
    except ValueError:
        return None
    return moment.astimezone().replace(tzinfo=None) if moment.tzinfo else moment


class LocalPictureFolder:
    """The storefront's image folder, when the ERP runs where it can write it (image_dir)."""

    def __init__(self, image_dir: str) -> None:
        self._root = Path(image_dir) if image_dir.strip() else None

    @property
    def enabled(self) -> bool:
        return self._root is not None and self._root.is_dir()

    def save(self, name: str, content: bytes) -> None:
        if self._root is None or not _PICTURE_NAME.fullmatch(name):
            raise DomainError("Ім'я картинки Instagram має бути числом з .jpg.")
        folder = self._root / "catalog" / "instagram"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(content)
