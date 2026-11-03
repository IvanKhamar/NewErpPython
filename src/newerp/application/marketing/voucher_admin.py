"""Gift vouchers by e-mail and the voucher themes (Python first, item 58): the old admin's "send" button and the
"Теми сертифікатів" list."""

import logging
import re
from typing import Protocol

from pydantic import Field

from newerp.application.common import Dto, NotFoundError
from newerp.application.marketing.vouchers import VoucherService
from newerp.domain.catalog import image_path
from newerp.domain.common import DomainError, plain

audit = logging.getLogger("newerp.audit")
_EMAIL = re.compile(r"[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+")


class MailSender(Protocol):
    @property
    def is_configured(self) -> bool: ...

    async def send(self, to: str, subject: str, text: str) -> None: ...


class VoucherMailResult(Dto):
    sent: bool
    to: str


class VoucherThemeDto(Dto):
    id: int = 0
    name: str = Field(default="", max_length=32)
    image: str = Field(default="", max_length=255)
    vouchers: int = 0


class VoucherThemeStore(Protocol):
    async def themes(self) -> list[VoucherThemeDto]: ...

    async def save(self, theme: VoucherThemeDto) -> int: ...

    async def delete(self, theme_id: int) -> None: ...


class VoucherAdminService:
    def __init__(self, vouchers: VoucherService, themes: VoucherThemeStore, mail: MailSender) -> None:
        self._vouchers = vouchers
        self._themes = themes
        self._mail = mail

    async def send_email(self, voucher_id: int, to: str | None, user_id: int) -> VoucherMailResult:
        """To the recipient's address of the voucher, or to the one given."""
        voucher = await self._vouchers.get(voucher_id)
        if not voucher.usable or voucher.balance <= 0:
            raise DomainError("Сертифікат зараз не можна використати, тож лист не надсилаємо.")
        address = (to or voucher.to_email).strip()
        if not _EMAIL.fullmatch(address):
            raise DomainError(f"E-mail отримувача «{address}» записано з помилкою.")
        if not self._mail.is_configured:
            raise DomainError("Пошту не налаштовано (NEWERP_MAIL__HOST і NEWERP_MAIL__FROM_ADDRESS).")
        sender = f" від {voucher.from_name.strip()}" if voucher.from_name.strip() else ""
        lines = [
            f"{voucher.to_name.strip() or 'Вітаємо'}!",
            "",
            f"Вам подарунковий сертифікат Vidro на {plain(voucher.balance, 2)} грн{sender}.",
            f"Код сертифіката: {voucher.code}",
        ]
        if voucher.message.strip():
            lines += ["", voucher.message.strip()]
        lines += ["", "Назвіть код менеджеру або введіть його під час оформлення замовлення на сайті."]
        await self._mail.send(address, "Подарунковий сертифікат Vidro", "\n".join(lines))
        audit.info("voucher e-mailed: %s, by user %s", voucher_id, user_id)
        return VoucherMailResult(sent=True, to=address)

    async def themes(self) -> list[VoucherThemeDto]:
        return await self._themes.themes()

    async def save_theme(self, theme_id: int | None, theme: VoucherThemeDto) -> VoucherThemeDto:
        if theme_id is not None and theme_id not in {t.id for t in await self._themes.themes()}:
            raise NotFoundError(f"Теми {theme_id} немає.")
        name = theme.name.strip()
        if not name:
            raise DomainError("Вкажіть назву теми.")
        image = image_path(theme.image)
        if not image:
            raise DomainError("Теми без картинки не буває: вкажіть шлях на кшталт catalog/vouchers/birthday.jpg.")
        saved = await self._themes.save(theme.model_copy(update={"id": theme_id or 0, "name": name, "image": image}))
        return next(t for t in await self._themes.themes() if t.id == saved)

    async def delete_theme(self, theme_id: int, user_id: int) -> None:
        theme = next((t for t in await self._themes.themes() if t.id == theme_id), None)
        if theme is None:
            raise NotFoundError(f"Теми {theme_id} немає.")
        if theme.vouchers:
            raise DomainError(f"Тему мають {theme.vouchers} сертифікатів: її не можна видалити.")
        await self._themes.delete(theme_id)
        audit.info("voucher theme deleted: %s, by user %s", theme_id, user_id)
