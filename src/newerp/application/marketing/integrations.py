"""The old site's marketing integrations (Python first, items 59–61): Klaviyo exports and switches, the TikTok
catalog feed, the Instagram block of the storefront.

Their API keys stay out of the ERP's pages: Klaviyo's keys stay in the storefront module, TikTok reads the feed with
a token from .env, and Instagram's token stays in dd_setting, where it is refreshed and saved every month as the old
module did. Pictures from Instagram are taken only from its own CDN hosts, over https, and only up to a size limit."""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal, Protocol

import nh3
from pydantic import Field

from newerp.application.common import Clock, Dto
from newerp.domain.common import DomainError
from newerp.domain.marketing import csv_text

audit = logging.getLogger("newerp.audit")

KlaviyoList = Literal["customers", "subscribers", "unsubscribers"]
TOKEN_REFRESH_DAYS = 30  # Instagram's long-lived tokens live 60 days; the old module refreshed them after 30
INSTAGRAM_HOSTS = (".cdninstagram.com", ".fbcdn.net")


# ── Klaviyo ──


@dataclass(frozen=True)
class KlaviyoContact:
    email: str
    firstname: str
    lastname: str
    telephone: str


class KlaviyoSettingsDto(Dto):
    """The storefront module's switches; its API keys are not shown or changed here."""

    enabled: bool = False
    tracking: bool = False  # the storefront sends "Viewed Product", "Started Checkout"… to Klaviyo
    newsletter_list_id: str = Field(default="", max_length=64)


class KlaviyoStore(Protocol):
    async def contacts(self, which: KlaviyoList) -> list[KlaviyoContact]:
        """Enabled customers: all, subscribed to the newsletter, or not subscribed; the newest first."""
        ...

    async def settings(self) -> KlaviyoSettingsDto: ...

    async def save_settings(self, settings: KlaviyoSettingsDto) -> None:
        """Only these keys of module_klaviyo; the other ones (the API keys) stay as they are."""
        ...


class KlaviyoService:
    def __init__(self, store: KlaviyoStore) -> None:
        self._store = store

    async def export(self, which: KlaviyoList, user_id: int) -> str:
        """CSV for Klaviyo's import ("," between columns): e-mails only for the newsletter lists, as the old one."""
        contacts = await self._store.contacts(which)
        if which == "customers":
            rows = [["email", "firstname", "lastname", "telephone"]]
            rows += [[c.email, c.firstname, c.lastname, c.telephone] for c in contacts]
        else:
            rows = [["email"]] + [[c.email] for c in contacts]
        audit.info("klaviyo export: %s, %s rows, by user %s", which, len(contacts), user_id)
        return csv_text(rows, ",")

    async def settings(self) -> KlaviyoSettingsDto:
        return await self._store.settings()

    async def save_settings(self, settings: KlaviyoSettingsDto) -> KlaviyoSettingsDto:
        list_id = settings.newsletter_list_id.strip()
        if list_id and not list_id.isalnum():
            raise DomainError("Номер списку Klaviyo: лише латинські літери й цифри.")
        await self._store.save_settings(settings.model_copy(update={"newsletter_list_id": list_id}))
        return await self._store.settings()


# ── TikTok catalog feed ──


@dataclass(frozen=True)
class FeedProduct:
    product_id: int
    name: str
    description: str
    price: Decimal
    quantity: int
    image: str
    seo_keyword: str


class FeedStore(Protocol):
    async def products(self) -> list[FeedProduct]:
        """Enabled products with a price and a picture."""
        ...


FEED_COLUMNS = ["sku_id", "title", "description", "availability", "condition", "price", "link", "image_link", "brand"]


class TikTokFeedService:
    def __init__(self, store: FeedStore, store_url: str, brand: str) -> None:
        self._store = store
        self._store_url = store_url.strip().rstrip("/")
        self._brand = brand

    async def csv(self) -> str:
        """TikTok's catalog template as CSV: one row per product, the description as plain text."""
        if not self._store_url:
            raise DomainError("Адресу вітрини не задано (NEWERP_CUSTOMERS__STORE_URL): без неї немає посилань.")
        rows = [FEED_COLUMNS]
        for p in await self._store.products():
            link = (
                f"{self._store_url}/{p.seo_keyword}"
                if p.seo_keyword
                else f"{self._store_url}/index.php?route=product/product&product_id={p.product_id}"
            )
            text = " ".join(nh3.clean(p.description, tags=set()).split())[:5000]
            rows.append([
                str(p.product_id), p.name[:255], text or p.name, "in stock" if p.quantity > 0 else "out of stock",
                "new", f"{p.price:.2f} UAH", link, f"{self._store_url}/image/{p.image}", self._brand,
            ])  # fmt: skip
        return csv_text(rows, ",")


# ── Instagram ──


@dataclass(frozen=True)
class InstagramMedia:
    id: str
    media_type: str  # IMAGE, VIDEO, CAROUSEL_ALBUM
    media_url: str
    thumbnail_url: str
    caption: str
    permalink: str
    timestamp: datetime | None


@dataclass(frozen=True)
class InstagramToken:
    value: str
    saved_at: datetime | None


class InstagramClient(Protocol):
    async def refresh(self, token: str) -> str: ...

    async def media(self, token: str) -> list[InstagramMedia]: ...

    async def download(self, url: str) -> bytes:
        """The picture's bytes; only https and Instagram's CDN hosts, refused above the size limit."""
        ...


class InstagramStore(Protocol):
    async def token(self) -> InstagramToken | None: ...

    async def save_token(self, token: str, now: datetime) -> None: ...

    async def known_ids(self) -> set[str]: ...

    async def add(self, media: InstagramMedia) -> None: ...


class PictureFolder(Protocol):
    @property
    def enabled(self) -> bool: ...

    def save(self, name: str, content: bytes) -> None:
        """Writes catalog/instagram/<name> under the storefront's image folder; name is digits and an extension."""
        ...


class InstagramUpdateResult(Dto):
    configured: bool
    token_refreshed: bool
    added: int
    pictures_saved: int


class InstagramService:
    def __init__(self, client: InstagramClient, store: InstagramStore, pictures: PictureFolder, clock: Clock) -> None:
        self._client = client
        self._store = store
        self._pictures = pictures
        self._clock = clock

    async def update(self) -> InstagramUpdateResult:
        """The old updateImages: refresh the token after a month, then add the posts the storefront does not have."""
        token = await self._store.token()
        if token is None or not token.value:
            return InstagramUpdateResult(configured=False, token_refreshed=False, added=0, pictures_saved=0)
        now = self._clock.now().replace(microsecond=0)
        value, refreshed = token.value, False
        if token.saved_at is None or now - token.saved_at > timedelta(days=TOKEN_REFRESH_DAYS):
            value = await self._client.refresh(token.value)
            await self._store.save_token(value, now)
            refreshed = True
        known = await self._store.known_ids()
        added = saved = 0
        for media in await self._client.media(value):
            if media.id in known or not media.id.isdigit():
                continue
            await self._store.add(media)
            added += 1
            if self._pictures.enabled and media.media_type == "IMAGE" and media.media_url:
                self._pictures.save(f"{media.id}.jpg", await self._client.download(media.media_url))
                saved += 1
        return InstagramUpdateResult(configured=True, token_refreshed=refreshed, added=added, pictures_saved=saved)
