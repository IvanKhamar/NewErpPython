"""Customer administration of the old admin (Python first): items 53 (the customer card: edit, unlock, log in as the
customer, balance transactions, reward points, IPs) and 54 (customer groups, approvals, custom fields).

Money and access are the sensitive parts: balance transactions and reward points go to the audit log, and logging
in as a customer is for admins only, through a one-time link that also expires after a few minutes."""

import logging
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal, Protocol

from pydantic import Field

from newerp.application.common import Clock, ConflictError, Dto, Money, NotFoundError
from newerp.domain.common import DomainError

audit = logging.getLogger("newerp.audit")
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
LOGIN_TOKEN_PREFIX = "erp-"  # erp-<unix time>-<random>: the time lets the ERP expire its own links


# ── 53: the customer card ──


class AddressEditDto(Dto):
    id: int = 0  # 0: a new address
    firstname: str = Field(default="", max_length=32)
    lastname: str = Field(default="", max_length=32)
    company: str = Field(default="", max_length=40)
    address_1: str = Field(default="", max_length=128)
    address_2: str = Field(default="", max_length=128)
    city: str = Field(default="", max_length=128)
    postcode: str = Field(default="", max_length=10)
    default: bool = False


class CustomerEditDto(Dto):
    """The editable part of a customer. The password is never set here: the customer resets it on the storefront."""

    firstname: str = Field(default="", max_length=32)
    lastname: str = Field(default="", max_length=32)
    email: str = Field(default="", max_length=96)
    telephone: str = Field(default="", max_length=32)
    customer_group_id: int = 1
    newsletter: bool = False
    enabled: bool = True
    safe: bool = False  # OpenCart's "safe": the fraud checks skip this customer
    addresses: list[AddressEditDto] = Field(default_factory=list, max_length=20)


class TransactionDto(Dto):
    id: int
    order_id: int
    description: str
    amount: Money
    date_added: datetime


class RewardDto(Dto):
    id: int
    order_id: int
    description: str
    points: int
    date_added: datetime


class AddTransactionRequest(Dto):
    """A positive amount adds to the customer's balance, a negative one takes from it."""

    description: str = Field(default="", max_length=255)
    amount: Decimal = Field(default=Decimal(0), ge=-1_000_000, le=1_000_000)


class AddRewardRequest(Dto):
    description: str = Field(default="", max_length=255)
    points: int = Field(default=0, ge=-1_000_000, le=1_000_000)


class CustomerIpDto(Dto):
    ip: str
    date_added: datetime | None
    customers: int  # how many customers used this IP: a shared or a suspicious one


class CustomerAccountDto(Dto):
    """Balance, reward points, IPs and failed logins of one customer."""

    customer_id: int
    balance: Money
    transactions: list[TransactionDto]
    reward_points: int
    rewards: list[RewardDto]
    ips: list[CustomerIpDto]
    failed_logins: int  # OpenCart locks the account after 5 within an hour


class LoginLinkDto(Dto):
    url: str
    expires_at: datetime


@dataclass(frozen=True)
class CustomerCore:
    customer_id: int
    email: str


# ── 54: groups, approvals, custom fields ──


class CustomerGroupEditDto(Dto):
    id: int = 0
    name: str = Field(default="", max_length=32)
    description: str = Field(default="", max_length=10_000)
    approval: bool = False  # new customers of this group wait for approval
    sort_order: int = 0
    customers: int = 0


class ApprovalDto(Dto):
    customer_id: int
    name: str
    email: str
    type: str  # "customer" or "affiliate"
    date_added: datetime


CustomFieldType = Literal["select", "radio", "checkbox", "text", "textarea", "date", "time", "datetime"]


class CustomFieldDto(Dto):
    id: int = 0
    name: str = Field(default="", max_length=128)
    type: CustomFieldType = "text"
    location: Literal["account", "address"] = "account"
    validation: str = Field(default="", max_length=255)  # a regular expression the storefront checks
    required: bool = False
    enabled: bool = True
    sort_order: int = 0
    customer_group_ids: list[int] = Field(default_factory=list, max_length=50)
    values: list[str] = Field(default_factory=list, max_length=100)  # choices of select, radio, checkbox


class CustomerAdminStore(Protocol):
    async def core(self, customer_id: int) -> CustomerCore | None: ...

    async def edit_form(self, customer_id: int) -> CustomerEditDto | None: ...

    async def email_owner(self, email: str) -> int | None: ...

    async def save(self, customer_id: int, form: CustomerEditDto) -> None:
        """The customer row and its addresses: listed ones are updated or added, the others deleted."""
        ...

    async def account(self, customer_id: int) -> CustomerAccountDto: ...

    async def add_transaction(self, customer_id: int, description: str, amount: Decimal, now: datetime) -> None: ...

    async def add_reward(self, customer_id: int, description: str, points: int, now: datetime) -> None: ...

    async def unlock(self, email: str) -> int:
        """Deletes the failed logins of this e-mail; returns how many records there were."""
        ...

    async def set_login_token(self, customer_id: int, token: str) -> None: ...

    async def expired_login_tokens(self) -> list[tuple[int, str]]:
        """(customer id, token) of every token the ERP made (they start with LOGIN_TOKEN_PREFIX)."""
        ...

    async def clear_login_token(self, customer_id: int, token: str) -> None:
        """Only if the customer still has this very token."""
        ...

    async def groups(self) -> list[CustomerGroupEditDto]: ...

    async def save_group(self, group: CustomerGroupEditDto) -> int: ...

    async def delete_group(self, group_id: int) -> None: ...

    async def default_group_id(self) -> int: ...

    async def approvals(self) -> list[ApprovalDto]: ...

    async def decide_approval(self, customer_id: int, approve: bool) -> bool:
        """Approve: the customer is enabled and the request removed; deny: only the request is removed."""
        ...

    async def custom_fields(self) -> list[CustomFieldDto]: ...

    async def save_custom_field(self, field: CustomFieldDto) -> int: ...

    async def delete_custom_field(self, field_id: int) -> bool: ...


class CustomerAdminService:
    def __init__(self, store: CustomerAdminStore, clock: Clock, store_url: str, link_minutes: int) -> None:
        self._store = store
        self._clock = clock
        self._store_url = store_url
        self._link_minutes = link_minutes

    async def edit_form(self, customer_id: int) -> CustomerEditDto:
        form = await self._store.edit_form(customer_id)
        if form is None:
            raise NotFoundError(f"Покупця {customer_id} не знайдено.")
        return form

    async def save(self, customer_id: int, form: CustomerEditDto, user_id: int) -> CustomerEditDto:
        current = await self.edit_form(customer_id)
        if not form.firstname.strip() and not form.lastname.strip():
            raise DomainError("Вкажіть ім'я або прізвище покупця.")
        email = form.email.strip().lower()
        if not _EMAIL.fullmatch(email):
            raise DomainError(f"E-mail «{email}» записано з помилкою.")
        owner = await self._store.email_owner(email)
        if owner is not None and owner != customer_id:
            raise ConflictError(f"E-mail {email} вже має покупець №{owner}.")
        if form.customer_group_id not in {g.id for g in await self._store.groups()}:
            raise DomainError(f"Групи покупців {form.customer_group_id} немає.")
        known = {a.id for a in current.addresses}
        unknown = {a.id for a in form.addresses if a.id} - known
        if unknown:
            raise DomainError(f"Адрес {', '.join(map(str, sorted(unknown)))} у покупця немає.")
        if sum(1 for a in form.addresses if a.default) > 1:
            raise DomainError("Основною може бути лише одна адреса.")
        clean = form.model_copy(
            update={
                "firstname": form.firstname.strip(),
                "lastname": form.lastname.strip(),
                "email": email,
                "telephone": form.telephone.strip(),
            }
        )
        await self._store.save(customer_id, clean)
        changed = [
            n
            for n in ("email", "telephone", "enabled", "customer_group_id")
            if getattr(current, n) != getattr(clean, n)
        ]
        audit.info("customer changed: %s, fields %s, by user %s", customer_id, ", ".join(changed), user_id)
        return await self.edit_form(customer_id)

    async def account(self, customer_id: int) -> CustomerAccountDto:
        await self._core(customer_id)
        return await self._store.account(customer_id)

    async def add_transaction(
        self, customer_id: int, request: AddTransactionRequest, user_id: int
    ) -> CustomerAccountDto:
        await self._core(customer_id)
        if request.amount == 0 or not request.description.strip():
            raise DomainError("Вкажіть суму (не нуль) і за що вона.")
        await self._store.add_transaction(customer_id, request.description.strip(), request.amount, self._now())
        audit.info("customer balance changed: %s, %s, by user %s", customer_id, request.amount, user_id)
        return await self._store.account(customer_id)

    async def add_reward(self, customer_id: int, request: AddRewardRequest, user_id: int) -> CustomerAccountDto:
        await self._core(customer_id)
        if request.points == 0 or not request.description.strip():
            raise DomainError("Вкажіть бали (не нуль) і за що вони.")
        await self._store.add_reward(customer_id, request.description.strip(), request.points, self._now())
        audit.info("customer reward points changed: %s, %s, by user %s", customer_id, request.points, user_id)
        return await self._store.account(customer_id)

    async def unlock(self, customer_id: int, user_id: int) -> int:
        """Lets a customer locked out after too many failed logins try again at once."""
        customer = await self._core(customer_id)
        removed = await self._store.unlock(customer.email)
        audit.info("customer unlocked: %s, by user %s", customer_id, user_id)
        return removed

    async def login_link(self, customer_id: int, user_id: int) -> LoginLinkDto:
        """A one-time link that logs the admin into the storefront as this customer (to see the cart or check an
        order). The storefront deletes the token once used; expire_login_links removes it after a few minutes."""
        await self._core(customer_id)
        if not self._store_url.strip():
            raise DomainError("Адресу вітрини не задано (NEWERP_CUSTOMERS__STORE_URL).")
        now = self._now()
        token = f"{LOGIN_TOKEN_PREFIX}{int(now.timestamp())}-{secrets.token_urlsafe(32)}"
        await self._store.set_login_token(customer_id, token)
        audit.warning("login as customer: %s, by user %s", customer_id, user_id)
        url = f"{self._store_url.strip().rstrip('/')}/index.php?route=account/login&token={token}"
        return LoginLinkDto(url=url, expires_at=now + timedelta(minutes=self._link_minutes))

    async def expire_login_links(self) -> int:
        """Removes the ERP's login tokens older than the link life; returns how many (a Celery beat task)."""
        cutoff = self._now().timestamp() - self._link_minutes * 60
        expired = 0
        for customer_id, token in await self._store.expired_login_tokens():
            parts = token.split("-")
            if len(parts) < 3 or not parts[1].isdigit() or int(parts[1]) <= cutoff:
                await self._store.clear_login_token(customer_id, token)
                expired += 1
        return expired

    # Groups

    async def groups(self) -> list[CustomerGroupEditDto]:
        return await self._store.groups()

    async def save_group(self, group_id: int | None, group: CustomerGroupEditDto) -> CustomerGroupEditDto:
        if group_id is not None and group_id not in {g.id for g in await self._store.groups()}:
            raise NotFoundError(f"Групи {group_id} немає.")
        name = group.name.strip()
        if not name:
            raise DomainError("Вкажіть назву групи.")
        saved = await self._store.save_group(group.model_copy(update={"id": group_id or 0, "name": name}))
        return next(g for g in await self._store.groups() if g.id == saved)

    async def delete_group(self, group_id: int, user_id: int) -> None:
        group = next((g for g in await self._store.groups() if g.id == group_id), None)
        if group is None:
            raise NotFoundError(f"Групи {group_id} немає.")
        if group_id == await self._store.default_group_id():
            raise DomainError("Це група за замовчуванням магазину: її не можна видалити.")
        if group.customers:
            raise DomainError(f"У групі {group.customers} покупців: спершу перенесіть їх.")
        await self._store.delete_group(group_id)
        audit.info("customer group deleted: %s, by user %s", group_id, user_id)

    # Approvals

    async def approvals(self) -> list[ApprovalDto]:
        return await self._store.approvals()

    async def decide(self, customer_id: int, approve: bool, user_id: int) -> None:
        if not await self._store.decide_approval(customer_id, approve):
            raise NotFoundError(f"Покупець {customer_id} не чекає на схвалення.")
        audit.info("customer %s: %s, by user %s", "approved" if approve else "denied", customer_id, user_id)

    # Custom fields

    async def custom_fields(self) -> list[CustomFieldDto]:
        return await self._store.custom_fields()

    async def save_custom_field(self, field_id: int | None, field: CustomFieldDto) -> CustomFieldDto:
        if field_id is not None and field_id not in {f.id for f in await self._store.custom_fields()}:
            raise NotFoundError(f"Поля {field_id} немає.")
        name = field.name.strip()
        if not name:
            raise DomainError("Вкажіть назву поля.")
        if field.validation:
            try:
                re.compile(field.validation)
            except re.error as error:
                raise DomainError(f"Перевірка поля — не регулярний вираз: {error}.") from error
        groups = {g.id for g in await self._store.groups()}
        unknown = set(field.customer_group_ids) - groups
        if unknown:
            raise DomainError(f"Груп покупців немає: {', '.join(map(str, sorted(unknown)))}.")
        choices = [v.strip() for v in field.values if v.strip()]
        if field.type in ("select", "radio", "checkbox") and not choices:
            raise DomainError("Для списку, перемикача чи прапорців потрібні варіанти.")
        clean = field.model_copy(update={"id": field_id or 0, "name": name, "values": choices})
        saved = await self._store.save_custom_field(clean)
        return next(f for f in await self._store.custom_fields() if f.id == saved)

    async def delete_custom_field(self, field_id: int, user_id: int) -> None:
        if not await self._store.delete_custom_field(field_id):
            raise NotFoundError(f"Поля {field_id} немає.")
        audit.info("custom field deleted: %s, by user %s", field_id, user_id)

    async def _core(self, customer_id: int) -> CustomerCore:
        customer = await self._store.core(customer_id)
        if customer is None:
            raise NotFoundError(f"Покупця {customer_id} не знайдено.")
        return customer

    def _now(self) -> datetime:
        return self._clock.now().replace(microsecond=0)
