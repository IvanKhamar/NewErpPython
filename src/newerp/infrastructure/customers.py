"""Customers straight from the OpenCart tables. Every query selects only the needed columns: the password, salt,
token and cart of a customer are never read."""

from datetime import datetime

from sqlalchemy import exists, func, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.application.common import PagedResult
from newerp.application.customers import (
    CustomerAddressDto,
    CustomerFilter,
    CustomerGroupDto,
    CustomerListItem,
    CustomerNoteDto,
    CustomerOrderItem,
    CustomerProfile,
)
from newerp.domain.orders import CustomerRisk
from newerp.infrastructure.models.customers import DdAddress, DdCustomer, DdCustomerGroupDescription, DdCustomerHistory
from newerp.infrastructure.models.orders import DdOrder, DdOrderStatus


class SqlCustomerStore:
    def __init__(self, session: AsyncSession, language_id: int) -> None:
        self._session = session
        self._language_id = language_id

    async def list_page(self, customer_filter: CustomerFilter) -> PagedResult[CustomerListItem]:
        c, f = DdCustomer, customer_filter
        name = func.concat(c.firstname, " ", c.lastname)
        conditions = []
        if f.search:
            in_phone = c.telephone.contains(f.search, autoescape=True)
            if f.search.isdigit():
                conditions.append((c.customer_id == int(f.search)) | in_phone)
            else:
                conditions.append(
                    name.contains(f.search, autoescape=True) | c.email.contains(f.search, autoescape=True) | in_phone
                )
        if f.group_id is not None:
            conditions.append(c.customer_group_id == f.group_id)
        if f.status is not None:
            conditions.append(c.status == f.status)
        total = await self._session.scalar(select(func.count()).select_from(c).where(*conditions))
        g = DdCustomerGroupDescription
        order_count = (
            select(func.count())
            .where(DdOrder.customer_id == c.customer_id, DdOrder.order_status_id > 0)
            .scalar_subquery()
        )
        rows = await self._session.execute(
            select(c.customer_id, name, c.email, c.telephone, g.name, c.status, c.date_added, order_count)
            .outerjoin(g, (g.customer_group_id == c.customer_group_id) & (g.language_id == self._language_id))
            .where(*conditions)
            .order_by(c.date_added.desc(), c.customer_id.desc())
            .offset((f.page - 1) * f.page_size)
            .limit(f.page_size)
        )
        items = [
            CustomerListItem(
                id=r[0],
                name=r[1],
                email=r[2],
                telephone=r[3],
                group_name=r[4],
                status=r[5],
                date_added=r[6],
                order_count=r[7],
            )  # fmt: skip
            for r in rows
        ]
        return PagedResult[CustomerListItem](items=items, page=f.page, page_size=f.page_size, total_count=total or 0)

    async def groups(self) -> list[CustomerGroupDto]:
        g = DdCustomerGroupDescription
        rows = await self._session.execute(
            select(g.customer_group_id, g.name).where(g.language_id == self._language_id).order_by(g.name)
        )
        return [CustomerGroupDto(id=i, name=name) for i, name in rows]

    async def get(self, customer_id: int) -> CustomerProfile | None:
        c = DdCustomer
        customer = (
            await self._session.execute(
                select(
                    c.customer_id, c.firstname, c.lastname, c.email, c.telephone, c.customer_group_id, c.status,
                    c.newsletter, c.address_id, c.date_added,
                ).where(c.customer_id == customer_id)
            )
        ).first()  # fmt: skip
        if customer is None:
            return None
        g = DdCustomerGroupDescription
        group = await self._session.scalar(
            select(g.name).where(g.customer_group_id == customer.customer_group_id, g.language_id == self._language_id)
        )
        a = DdAddress
        addresses = await self._session.execute(
            select(a.address_id, func.concat(a.firstname, " ", a.lastname), a.city, a.address_1, a.address_2)
            .where(a.customer_id == customer_id)
            .order_by(a.address_id)
        )
        h = DdCustomerHistory
        notes = await self._session.execute(
            select(h.date_added, h.comment)
            .where(h.customer_id == customer_id)
            .order_by(h.date_added.desc(), h.customer_history_id.desc())
        )
        return CustomerProfile(
            id=customer.customer_id, firstname=customer.firstname, lastname=customer.lastname, email=customer.email,
            telephone=customer.telephone, group_name=group, status=customer.status, newsletter=customer.newsletter,
            date_added=customer.date_added,
            addresses=[
                CustomerAddressDto(
                    id=i, name=name, city=city, address1=address1, address2=address2,
                    is_default=i == customer.address_id,
                )  # fmt: skip
                for i, name, city, address1, address2 in addresses
            ],
            notes=[CustomerNoteDto(date_added=added, comment=comment) for added, comment in notes],
        )  # fmt: skip

    async def orders(self, customer_id: int, phone_key: str | None) -> list[CustomerOrderItem]:
        o, s = DdOrder, DdOrderStatus
        mine = o.customer_id == customer_id
        if phone_key is not None:
            # Phones in orders are written any way, so the database narrows by digits and Python compares exactly.
            digits = o.telephone
            for char in "+- ()":
                digits = func.replace(digits, char, "")
            mine = mine | digits.endswith(phone_key, autoescape=True)
        rows = await self._session.execute(
            select(
                o.order_id,
                o.date_added,
                o.order_status_id,
                s.name,
                o.total,
                o.currency_code,
                o.customer_id,
                o.telephone,
            )
            .outerjoin(s, (s.order_status_id == o.order_status_id) & (s.language_id == self._language_id))
            .where(o.order_status_id > 0, mine)
            .order_by(o.date_added.desc(), o.order_id.desc())
        )
        return [
            CustomerOrderItem(
                id=r.order_id,
                date_added=r.date_added,
                status_id=r.order_status_id,
                status_name=r.name,
                total=r.total,
                currency_code=r.currency_code,
                by_phone=r.customer_id != customer_id,
            )  # fmt: skip
            for r in rows
            if r.customer_id == customer_id or CustomerRisk.phone_key(r.telephone) == phone_key
        ]

    async def add_note(self, customer_id: int, comment: str, now: datetime) -> bool:
        if not await self._session.scalar(select(exists().where(DdCustomer.customer_id == customer_id))):
            return False
        await self._session.execute(
            insert(DdCustomerHistory).values(customer_id=customer_id, comment=comment, date_added=now)
        )
        await self._session.commit()
        return True
