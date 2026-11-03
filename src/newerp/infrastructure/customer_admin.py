"""Customer administration over the OpenCart tables: the customer and its addresses, balance transactions, reward
points, IPs, failed logins, the login token, groups, approvals and custom fields."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, Text, delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.application.customer_admin import (
    LOGIN_TOKEN_PREFIX,
    AddressEditDto,
    ApprovalDto,
    CustomerAccountDto,
    CustomerCore,
    CustomerEditDto,
    CustomerGroupEditDto,
    CustomerIpDto,
    CustomFieldDto,
    RewardDto,
    TransactionDto,
)
from newerp.infrastructure.models.customers import DdAddress, DdCustomer
from newerp.infrastructure.models.settings import DdSetting
from newerp.infrastructure.orders.credits import customer_reward, customer_transaction

metadata = MetaData()
customer_ip = Table(
    "dd_customer_ip",
    metadata,
    Column("customer_ip_id", Integer, primary_key=True),
    Column("customer_id", Integer),
    Column("ip", String(40)),
    Column("date_added", DateTime),
)
customer_login = Table(
    "dd_customer_login",
    metadata,
    Column("customer_login_id", Integer, primary_key=True),
    Column("email", String(96)),
    Column("ip", String(40)),
    Column("total", Integer),
    Column("date_added", DateTime),
    Column("date_modified", DateTime),
)
customer_approval = Table(
    "dd_customer_approval",
    metadata,
    Column("customer_approval_id", Integer, primary_key=True),
    Column("customer_id", Integer),
    Column("type", String(9)),
    Column("date_added", DateTime),
)
customer_group = Table(
    "dd_customer_group",
    metadata,
    Column("customer_group_id", Integer, primary_key=True),
    Column("approval", Integer),
    Column("sort_order", Integer),
)
customer_group_description = Table(
    "dd_customer_group_description",
    metadata,
    Column("customer_group_id", Integer, primary_key=True),
    Column("language_id", Integer, primary_key=True),
    Column("name", String(32)),
    Column("description", Text),
)
custom_field = Table(
    "dd_custom_field",
    metadata,
    Column("custom_field_id", Integer, primary_key=True),
    Column("type", String(32)),
    Column("value", Text),
    Column("validation", String(255)),
    Column("location", String(10)),
    Column("status", Integer),
    Column("sort_order", Integer),
)
custom_field_description = Table(
    "dd_custom_field_description",
    metadata,
    Column("custom_field_id", Integer, primary_key=True),
    Column("language_id", Integer, primary_key=True),
    Column("name", String(128)),
)
custom_field_value = Table(
    "dd_custom_field_value",
    metadata,
    Column("custom_field_value_id", Integer, primary_key=True),
    Column("custom_field_id", Integer),
    Column("sort_order", Integer),
)
custom_field_value_description = Table(
    "dd_custom_field_value_description",
    metadata,
    Column("custom_field_value_id", Integer, primary_key=True),
    Column("language_id", Integer, primary_key=True),
    Column("custom_field_id", Integer),
    Column("name", String(128)),
)
custom_field_customer_group = Table(
    "dd_custom_field_customer_group",
    metadata,
    Column("custom_field_id", Integer, primary_key=True),
    Column("customer_group_id", Integer, primary_key=True),
    Column("required", Integer),
)

ADDRESS_FIELDS = ("firstname", "lastname", "company", "address_1", "address_2", "city", "postcode")


class SqlCustomerAdminStore:
    def __init__(self, session: AsyncSession, language_id: int) -> None:
        self._session = session
        self._language_id = language_id

    # The customer

    async def core(self, customer_id: int) -> CustomerCore | None:
        row = (
            await self._session.execute(
                select(DdCustomer.customer_id, DdCustomer.email).where(DdCustomer.customer_id == customer_id)
            )
        ).first()
        return None if row is None else CustomerCore(row[0], row[1])

    async def edit_form(self, customer_id: int) -> CustomerEditDto | None:
        c = DdCustomer
        row = (
            await self._session.execute(
                select(
                    c.firstname,
                    c.lastname,
                    c.email,
                    c.telephone,
                    c.customer_group_id,
                    c.newsletter,
                    c.status,
                    c.safe,
                    c.address_id,
                ).where(c.customer_id == customer_id)
            )
        ).first()
        if row is None:
            return None
        a = DdAddress
        addresses = await self._session.execute(
            select(a.address_id, *(getattr(a, n) for n in ADDRESS_FIELDS)).where(a.customer_id == customer_id)
        )
        return CustomerEditDto(
            firstname=row[0],
            lastname=row[1],
            email=row[2],
            telephone=row[3],
            customer_group_id=row[4],
            newsletter=row[5],
            enabled=row[6],
            safe=row[7],
            addresses=[
                AddressEditDto(
                    id=r[0], default=r[0] == row[8], **dict(zip(ADDRESS_FIELDS, (v or "" for v in r[1:]), strict=True))
                )
                for r in addresses
            ],
        )

    async def email_owner(self, email: str) -> int | None:
        return await self._session.scalar(
            select(DdCustomer.customer_id).where(func.lower(DdCustomer.email) == email).limit(1)
        )

    async def save(self, customer_id: int, form: CustomerEditDto) -> None:
        s, a = self._session, DdAddress
        kept = [address.id for address in form.addresses if address.id]
        await s.execute(delete(a).where(a.customer_id == customer_id, a.address_id.not_in(kept or [0])))
        default_id = first_id = 0
        for address in form.addresses:
            values = {name: getattr(address, name) for name in ADDRESS_FIELDS}
            if address.id:
                await s.execute(
                    update(a).where(a.address_id == address.id, a.customer_id == customer_id).values(values)
                )
                address_id = address.id
            else:
                query = insert(a).values(
                    {**values, "customer_id": customer_id, "country_id": 0, "zone_id": 0, "custom_field": ""}
                )
                address_id = (await s.execute(query)).inserted_primary_key[0]
            first_id = first_id or address_id
            if address.default:
                default_id = address_id
        await s.execute(
            update(DdCustomer)
            .where(DdCustomer.customer_id == customer_id)
            .values(
                firstname=form.firstname,
                lastname=form.lastname,
                email=form.email,
                telephone=form.telephone,
                customer_group_id=form.customer_group_id,
                newsletter=form.newsletter,
                status=form.enabled,
                safe=form.safe,
                address_id=default_id or first_id,
            )
        )
        await s.commit()

    # Account: balance, reward points, IPs, failed logins

    async def account(self, customer_id: int) -> CustomerAccountDto:
        t, r = customer_transaction.c, customer_reward.c
        transactions = await self._session.execute(
            select(t.customer_transaction_id, t.order_id, t.description, t.amount, t.date_added)
            .where(t.customer_id == customer_id)
            .order_by(t.date_added.desc())
        )
        rewards = await self._session.execute(
            select(r.customer_reward_id, r.order_id, r.description, r.points, r.date_added)
            .where(r.customer_id == customer_id)
            .order_by(r.date_added.desc())
        )
        # Each IP of this customer with how many customers in all used it (a shared or a suspicious address).
        ip_table = customer_ip.alias("own")
        o = ip_table.c
        ips = await self._session.execute(
            select(
                o.ip,
                func.max(o.date_added),
                select(func.count(func.distinct(customer_ip.c.customer_id)))
                .where(customer_ip.c.ip == o.ip)
                .scalar_subquery(),
            )
            .where(o.customer_id == customer_id)
            .group_by(o.ip)
            .order_by(func.max(o.date_added).desc())
        )
        email = await self._session.scalar(select(DdCustomer.email).where(DdCustomer.customer_id == customer_id))
        failed = await self._session.scalar(
            select(func.coalesce(func.sum(customer_login.c.total), 0)).where(customer_login.c.email == email)
        )
        transaction_rows = [
            TransactionDto(id=x[0], order_id=x[1], description=x[2], amount=x[3], date_added=x[4]) for x in transactions
        ]
        reward_rows = [
            RewardDto(id=x[0], order_id=x[1], description=x[2], points=x[3], date_added=x[4]) for x in rewards
        ]
        return CustomerAccountDto(
            customer_id=customer_id,
            balance=sum((x.amount for x in transaction_rows), Decimal(0)),
            transactions=transaction_rows,
            reward_points=sum(x.points for x in reward_rows),
            rewards=reward_rows,
            ips=[CustomerIpDto(ip=x[0], date_added=x[1], customers=x[2]) for x in ips],
            failed_logins=int(failed or 0),
        )

    async def add_transaction(self, customer_id: int, description: str, amount: Decimal, now: datetime) -> None:
        await self._session.execute(
            insert(customer_transaction).values(
                customer_id=customer_id, order_id=0, description=description, amount=amount, date_added=now
            )
        )
        await self._session.commit()

    async def add_reward(self, customer_id: int, description: str, points: int, now: datetime) -> None:
        await self._session.execute(
            insert(customer_reward).values(
                customer_id=customer_id, order_id=0, description=description, points=points, date_added=now
            )
        )
        await self._session.commit()

    async def unlock(self, email: str) -> int:
        result = await self._session.execute(delete(customer_login).where(customer_login.c.email == email))
        await self._session.commit()
        return result.rowcount

    # The login token

    async def set_login_token(self, customer_id: int, token: str) -> None:
        await self._session.execute(update(DdCustomer).where(DdCustomer.customer_id == customer_id).values(token=token))
        await self._session.commit()

    async def expired_login_tokens(self) -> list[tuple[int, str]]:
        rows = await self._session.execute(
            select(DdCustomer.customer_id, DdCustomer.token).where(
                DdCustomer.token.startswith(LOGIN_TOKEN_PREFIX, autoescape=True)
            )
        )
        return [(customer_id, token) for customer_id, token in rows]

    async def clear_login_token(self, customer_id: int, token: str) -> None:
        await self._session.execute(
            update(DdCustomer).where(DdCustomer.customer_id == customer_id, DdCustomer.token == token).values(token="")
        )
        await self._session.commit()

    # Groups

    async def groups(self) -> list[CustomerGroupEditDto]:
        g, d = customer_group.c, customer_group_description.c
        customers = select(func.count()).where(DdCustomer.customer_group_id == g.customer_group_id).scalar_subquery()
        rows = await self._session.execute(
            select(g.customer_group_id, d.name, d.description, g.approval, g.sort_order, customers)
            .outerjoin(
                customer_group_description,
                (d.customer_group_id == g.customer_group_id) & (d.language_id == self._language_id),
            )
            .order_by(g.sort_order, d.name)
        )
        return [
            CustomerGroupEditDto(
                id=r[0], name=r[1] or "", description=r[2] or "", approval=bool(r[3]), sort_order=r[4], customers=r[5]
            )
            for r in rows
        ]

    async def save_group(self, group: CustomerGroupEditDto) -> int:
        s, c = self._session, customer_group.c
        values = {"approval": int(group.approval), "sort_order": group.sort_order}
        if group.id:
            await s.execute(update(customer_group).where(c.customer_group_id == group.id).values(values))
            group_id = group.id
        else:
            group_id = (await s.execute(insert(customer_group).values(values))).inserted_primary_key[0]
        d = customer_group_description.c
        await s.execute(
            delete(customer_group_description).where(
                d.customer_group_id == group_id, d.language_id == self._language_id
            )
        )
        await s.execute(
            insert(customer_group_description).values(
                customer_group_id=group_id,
                language_id=self._language_id,
                name=group.name,
                description=group.description,
            )
        )
        await s.commit()
        return group_id

    async def delete_group(self, group_id: int) -> None:
        for table in (customer_group_description, custom_field_customer_group, customer_group):
            await self._session.execute(delete(table).where(table.c.customer_group_id == group_id))
        await self._session.commit()

    async def default_group_id(self) -> int:
        value = await self._session.scalar(
            select(DdSetting.value).where(
                DdSetting.store_id == 0, DdSetting.code == "config", DdSetting.key == "config_customer_group_id"
            )
        )
        return int(value) if value and value.isdigit() else 1

    # Approvals

    async def approvals(self) -> list[ApprovalDto]:
        a, c = customer_approval.c, DdCustomer
        rows = await self._session.execute(
            select(a.customer_id, func.concat(c.firstname, " ", c.lastname), c.email, a.type, a.date_added)
            .join(DdCustomer, c.customer_id == a.customer_id)
            .order_by(a.date_added)
        )
        return [ApprovalDto(customer_id=r[0], name=r[1].strip(), email=r[2], type=r[3], date_added=r[4]) for r in rows]

    async def decide_approval(self, customer_id: int, approve: bool) -> bool:
        result = await self._session.execute(
            delete(customer_approval).where(customer_approval.c.customer_id == customer_id)
        )
        if result.rowcount and approve:
            await self._session.execute(
                update(DdCustomer).where(DdCustomer.customer_id == customer_id).values(status=True)
            )
        await self._session.commit()
        return result.rowcount > 0

    # Custom fields

    async def custom_fields(self) -> list[CustomFieldDto]:
        f, d = custom_field.c, custom_field_description.c
        rows = (
            await self._session.execute(
                select(f.custom_field_id, d.name, f.type, f.location, f.validation, f.status, f.sort_order)
                .outerjoin(
                    custom_field_description,
                    (d.custom_field_id == f.custom_field_id) & (d.language_id == self._language_id),
                )
                .order_by(f.sort_order, d.name)
            )
        ).all()
        groups: dict[int, list[tuple[int, int]]] = {}
        for field_id, group_id, required in await self._session.execute(select(custom_field_customer_group)):
            groups.setdefault(field_id, []).append((group_id, required))
        values: dict[int, list[str]] = {}
        v, vd = custom_field_value.c, custom_field_value_description.c
        for field_id, name in await self._session.execute(
            select(v.custom_field_id, vd.name)
            .join(
                custom_field_value_description,
                (vd.custom_field_value_id == v.custom_field_value_id) & (vd.language_id == self._language_id),
            )
            .order_by(v.sort_order)
        ):
            values.setdefault(field_id, []).append(name)
        return [
            CustomFieldDto(
                id=r[0],
                name=r[1] or "",
                type=r[2],
                location=r[3],
                validation=r[4] or "",
                enabled=bool(r[5]),
                sort_order=r[6],
                customer_group_ids=[g for g, _ in groups.get(r[0], [])],
                required=any(required for _, required in groups.get(r[0], [])),
                values=values.get(r[0], []),
            )
            for r in rows
        ]

    async def save_custom_field(self, field: CustomFieldDto) -> int:
        s, f = self._session, custom_field.c
        values = {
            "type": field.type,
            "value": "",
            "validation": field.validation,
            "location": field.location,
            "status": int(field.enabled),
            "sort_order": field.sort_order,
        }
        if field.id:
            await s.execute(update(custom_field).where(f.custom_field_id == field.id).values(values))
            field_id = field.id
        else:
            field_id = (await s.execute(insert(custom_field).values(values))).inserted_primary_key[0]
        await self._clear_field_links(field_id)
        await s.execute(
            insert(custom_field_description).values(
                custom_field_id=field_id, language_id=self._language_id, name=field.name
            )
        )
        if field.customer_group_ids:
            await s.execute(
                insert(custom_field_customer_group),
                [
                    {"custom_field_id": field_id, "customer_group_id": g, "required": int(field.required)}
                    for g in field.customer_group_ids
                ],
            )
        for order, name in enumerate(field.values):
            value_id = (
                await s.execute(insert(custom_field_value).values(custom_field_id=field_id, sort_order=order))
            ).inserted_primary_key[0]
            await s.execute(
                insert(custom_field_value_description).values(
                    custom_field_value_id=value_id, language_id=self._language_id, custom_field_id=field_id, name=name
                )
            )
        await s.commit()
        return field_id

    async def delete_custom_field(self, field_id: int) -> bool:
        result = await self._session.execute(delete(custom_field).where(custom_field.c.custom_field_id == field_id))
        await self._clear_field_links(field_id)
        await self._session.commit()
        return result.rowcount > 0

    async def _clear_field_links(self, field_id: int) -> None:
        for table in (
            custom_field_description,
            custom_field_customer_group,
            custom_field_value_description,
            custom_field_value,
        ):
            await self._session.execute(delete(table).where(table.c.custom_field_id == field_id))
