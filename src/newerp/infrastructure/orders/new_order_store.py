from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from sqlalchemy import String, and_, delete, exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from newerp.application.orders.models import (
    CustomerPickDto,
    NewOrderLineRecord,
    NewOrderRecord,
    OrderCompositionDto,
    OrderCompositionLine,
    OrderMethodDto,
    OrderMethodsDto,
    ProductOptionValueDto,
    ProductPickDto,
)
from newerp.domain.orders import CustomerRisk, OrderDraft
from newerp.infrastructure.models.catalog import (
    DdOptionDescription,
    DdOptionValueDescription,
    DdProduct,
    DdProductDescription,
    DdProductOptionValue,
)
from newerp.infrastructure.models.orders import DdOrder, DdOrderOption, DdOrderProduct, DdOrderSource, DdOrderTotal
from newerp.infrastructure.orders.queries import parse_int

# Ukraine's id in the standard OpenCart country list; used only while the database has no order yet.
UKRAINE_COUNTRY_ID = 220


class SqlNewOrderStore:
    def __init__(self, session: AsyncSession, language_id: int) -> None:
        self._session = session
        self._language_id = language_id

    async def search_products(self, term: str, limit: int) -> list[ProductPickDto]:
        product_id = parse_int(term) or 0
        ids = await self._session.scalars(
            select(DdProduct.product_id)
            .join(DdProductDescription, self._description_of(DdProduct.product_id))
            .where(
                DdProduct.status.is_(True),
                or_(
                    DdProduct.product_id == product_id,
                    DdProductDescription.name.contains(term, autoescape=True),
                    DdProduct.model.contains(term, autoescape=True),
                ),
            )
            .order_by(DdProductDescription.name)
            .limit(limit)
        )
        return await self.get_products(list(ids))

    async def get_products(self, product_ids: list[int]) -> list[ProductPickDto]:
        if not product_ids:
            return []
        # Only the needed columns: date_available may be '0000-00-00' in the real database.
        products = await self._session.execute(
            select(
                DdProduct.product_id, DdProductDescription.name, DdProduct.model, DdProduct.price, DdProduct.quantity
            )
            .join(DdProductDescription, self._description_of(DdProduct.product_id))
            .where(DdProduct.product_id.in_(product_ids))
            .order_by(DdProductDescription.name)
        )
        options = await self._session.execute(
            select(
                DdProductOptionValue.product_id,
                DdProductOptionValue.product_option_id,
                DdProductOptionValue.product_option_value_id,
                func.coalesce(DdOptionDescription.name, ""),
                func.coalesce(DdOptionValueDescription.name, ""),
                DdProductOptionValue.quantity,
                DdProductOptionValue.price,
            )
            .outerjoin(
                DdOptionDescription,
                and_(
                    DdOptionDescription.option_id == DdProductOptionValue.option_id,
                    DdOptionDescription.language_id == self._language_id,
                ),
            )
            .outerjoin(
                DdOptionValueDescription,
                and_(
                    DdOptionValueDescription.option_value_id == DdProductOptionValue.option_value_id,
                    DdOptionValueDescription.language_id == self._language_id,
                ),
            )
            .where(DdProductOptionValue.product_id.in_(product_ids))
            .order_by(DdProductOptionValue.product_option_value_id)
        )
        by_product: dict[int, list[ProductOptionValueDto]] = defaultdict(list)
        for product_id, option_id, value_id, option_name, value, quantity, price in options:
            by_product[product_id].append(
                ProductOptionValueDto(
                    product_option_id=option_id,
                    product_option_value_id=value_id,
                    option_name=option_name,
                    value=value,
                    quantity=quantity,
                    price=price,
                )
            )
        return [
            ProductPickDto(id=pid, name=name, model=model, price=price, quantity=quantity, options=by_product[pid])
            for pid, name, model, price, quantity in products
        ]

    async def search_customers(self, term: str, limit: int) -> list[CustomerPickDto]:
        rows = await self._session.scalars(
            select(DdOrder)
            .where(
                DdOrder.order_status_id > 0,
                or_(
                    DdOrder.telephone.contains(term, autoescape=True),
                    DdOrder.email.contains(term, autoescape=True),
                    DdOrder.firstname.contains(term, autoescape=True),
                    DdOrder.lastname.contains(term, autoescape=True),
                    func.concat(DdOrder.firstname, " ", DdOrder.lastname).contains(term, autoescape=True),
                ),
            )
            .order_by(DdOrder.order_id.desc())
            .limit(limit * 5)
        )
        # One customer, one row (the data of their latest order).
        customers: dict[str, CustomerPickDto] = {}
        for o in rows:
            key = CustomerRisk.phone_key(o.telephone) or f"{o.firstname} {o.lastname} {o.email}".lower()
            if key not in customers:
                customers[key] = CustomerPickDto(
                    customer_id=o.customer_id,
                    firstname=o.firstname,
                    lastname=o.lastname,
                    telephone=o.telephone,
                    email=o.email,
                    instagram=o.instagram,
                    city=o.shipping_city,
                    address1=o.shipping_address_1,
                    address2=o.shipping_address_2,
                    last_order_id=o.order_id,
                )
        return list(customers.values())[:limit]

    async def find_customer_orders(
        self, phone_key: str | None, firstname: str | None, lastname: str | None, email: str | None, since: datetime
    ) -> list[tuple[int, int]]:
        # Phones are stored any way ("+38 (067) 123-45-67"), so only the digits are compared, as the old site did.
        digits = DdOrder.telephone
        for char in "+- ()":
            digits = func.replace(digits, char, "", type_=String)
        matches = []
        if phone_key is not None:
            matches.append(digits.endswith(phone_key, autoescape=True))
        if firstname is not None and lastname is not None:
            matches.append(and_(DdOrder.firstname == firstname, DdOrder.lastname == lastname))
        if email is not None:
            matches.append(DdOrder.email == email)

        rows = await self._session.execute(
            select(
                DdOrder.order_id,
                DdOrder.order_status_id,
                DdOrder.telephone,
                DdOrder.firstname,
                DdOrder.lastname,
                DdOrder.email,
            )
            .where(DdOrder.date_added >= since, DdOrder.order_status_id > 0, or_(*matches))
            .order_by(DdOrder.date_added.desc())
        )

        def same(a: str, b: str) -> bool:
            return a.strip().casefold() == b.casefold()

        return [
            (order_id, status_id)
            for order_id, status_id, telephone, first, last, mail in rows
            if (phone_key is not None and CustomerRisk.phone_key(telephone) == phone_key)
            or (firstname is not None and lastname is not None and same(first, firstname) and same(last, lastname))
            or (email is not None and same(mail, email))
        ]

    async def list_methods(self) -> OrderMethodsDto:
        async def methods(code, name) -> list[OrderMethodDto]:
            rows = await self._session.execute(
                select(code, name, func.count()).where(name != "").group_by(code, name).order_by(func.count().desc())
            )
            return [OrderMethodDto(code=c, name=n) for c, n, _ in rows]

        return OrderMethodsDto(
            shipping=await methods(DdOrder.shipping_code, DdOrder.shipping_method),
            payment=await methods(DdOrder.payment_code, DdOrder.payment_method),
        )

    async def source_exists(self, source_id: int) -> bool:
        return bool(await self._session.scalar(select(exists().where(DdOrderSource.order_source_id == source_id))))

    async def create(self, order: NewOrderRecord) -> int:
        r = order.request
        # Shop fields (name, currency, country, address format) come from OpenCart settings NewErp does not have,
        # so they are taken from the site's latest order.
        template = await self._session.scalar(select(DdOrder).order_by(DdOrder.order_id.desc()).limit(1))

        def text(value: str | None) -> str:
            return (value or "").strip()

        firstname, lastname = text(r.firstname), text(r.lastname)
        city, address1, address2 = text(r.city), text(r.address1), text(r.address2)
        country = template.shipping_country if template else "Україна"
        country_id = template.shipping_country_id if template else UKRAINE_COUNTRY_ID
        zone = template.shipping_zone if template else ""
        zone_id = template.shipping_zone_id if template else 0
        address_format = template.shipping_address_format if template else ""

        row = DdOrder(
            invoice_prefix=template.invoice_prefix if template else "",
            store_id=template.store_id if template else 0,
            store_name=template.store_name if template else "",
            store_url=template.store_url if template else "",
            firstname=firstname,
            lastname=lastname,
            email=text(r.email),
            telephone=text(r.telephone),
            instagram=text(r.instagram),
            custom_field="[]",
            payment_custom_field="[]",
            shipping_custom_field="[]",
            payment_method=text(r.payment_method),
            payment_code=text(r.payment_code),
            shipping_method=text(r.shipping_method),
            shipping_code=text(r.shipping_code),
            shipping_house=text(r.house),
            shipping_flat=text(r.flat),
            comment=text(r.comment),
            total=order.draft.total,
            prepayment=order.draft.prepayment,
            order_status_id=0,
            order_source_id=r.order_source_id,
            language_id=template.language_id if template else self._language_id,
            currency_id=template.currency_id if template else 0,
            currency_code=template.currency_code if template else "UAH",
            currency_value=template.currency_value if template else Decimal(1),
            user_agent="NewErp",
            date_added=order.created_at,
            date_modified=order.created_at,
            # As on the old site: the payment and shipping addresses are the same.
            payment_firstname=firstname,
            shipping_firstname=firstname,
            payment_lastname=lastname,
            shipping_lastname=lastname,
            payment_city=city,
            shipping_city=city,
            payment_address_1=address1,
            shipping_address_1=address1,
            payment_address_2=address2,
            shipping_address_2=address2,
            payment_country=country,
            shipping_country=country,
            payment_country_id=country_id,
            shipping_country_id=country_id,
            payment_zone=zone,
            shipping_zone=zone,
            payment_zone_id=zone_id,
            shipping_zone_id=zone_id,
            payment_address_format=address_format,
            shipping_address_format=address_format,
        )
        # MyISAM, no transactions: saved step by step (the order first, to get its id).
        self._session.add(row)
        await self._session.commit()
        await self._add_lines(row.order_id, row.shipping_method, order.draft, order.lines)
        return row.order_id

    async def get_composition(self, order_id: int) -> OrderCompositionDto | None:
        order = (
            await self._session.execute(
                select(DdOrder.order_status_id, DdOrder.prepayment, DdOrder.total).where(DdOrder.order_id == order_id)
            )
        ).first()
        if order is None:
            return None
        status_id, prepayment, total = order

        products = list(
            await self._session.scalars(
                select(DdOrderProduct)
                .where(DdOrderProduct.order_id == order_id)
                .order_by(DdOrderProduct.order_product_id)
            )
        )
        options: dict[int, list[DdOrderOption]] = defaultdict(list)
        for option in await self._session.scalars(
            select(DdOrderOption).where(DdOrderOption.order_id == order_id).order_by(DdOrderOption.order_option_id)
        ):
            options[option.order_product_id].append(option)
        totals = (
            await self._session.execute(
                select(DdOrderTotal.code, DdOrderTotal.value).where(DdOrderTotal.order_id == order_id)
            )
        ).all()
        catalog = {p.id: p for p in await self.get_products(list(dict.fromkeys(p.product_id for p in products)))}

        # The size is the line's first option (the new-order form writes one). OpenCart writes a coupon as negative.
        lines = []
        for p in products:
            option = options[p.order_product_id][0] if options[p.order_product_id] else None
            lines.append(
                OrderCompositionLine(
                    product_id=p.product_id,
                    product_option_value_id=option.product_option_value_id if option else None,
                    name=p.name,
                    model=p.model,
                    option=option.value if option else "",
                    quantity=p.quantity,
                    price=p.price,
                    options=catalog[p.product_id].options if p.product_id in catalog else [],
                )
            )
        return OrderCompositionDto(
            order_id=order_id,
            status_id=status_id,
            lines=lines,
            shipping_cost=sum((v for c, v in totals if c == "shipping"), Decimal(0)),
            discount=-sum((v for c, v in totals if c == "coupon"), Decimal(0)),
            prepayment=prepayment,
            total=total,
        )

    async def replace_lines(
        self, order_id: int, draft: OrderDraft, lines: list[NewOrderLineRecord], now: datetime
    ) -> None:
        shipping_method = await self._session.scalar(
            select(DdOrder.shipping_method).where(DdOrder.order_id == order_id)
        )
        # Like editOrder of the old site: lines, options and totals are deleted and written again (MyISAM).
        # A used gift voucher keeps its total row (and dd_voucher_history), and the new sum is smaller by it.
        voucher = await self.voucher_paid(order_id)
        await self._session.execute(delete(DdOrderOption).where(DdOrderOption.order_id == order_id))
        await self._session.execute(delete(DdOrderProduct).where(DdOrderProduct.order_id == order_id))
        await self._session.execute(
            delete(DdOrderTotal).where(DdOrderTotal.order_id == order_id, DdOrderTotal.code != "voucher")
        )
        await self._session.commit()
        await self._add_lines(order_id, shipping_method or "", draft, lines)

        total = draft.total - voucher
        if voucher > 0:
            await self._session.execute(
                update(DdOrderTotal)
                .where(DdOrderTotal.order_id == order_id, DdOrderTotal.code == "total")
                .values(value=total, sort_order=99)
            )
        await self._session.execute(
            update(DdOrder)
            .where(DdOrder.order_id == order_id)
            .values(total=total, prepayment=draft.prepayment, date_modified=now)
        )
        await self._session.commit()

    async def voucher_paid(self, order_id: int) -> Decimal:
        paid = await self._session.scalar(
            select(func.sum(DdOrderTotal.value)).where(
                DdOrderTotal.order_id == order_id, DdOrderTotal.code == "voucher"
            )
        )
        return -(paid or Decimal(0))

    async def _add_lines(
        self, order_id: int, shipping_method: str, draft: OrderDraft, lines: list[NewOrderLineRecord]
    ) -> None:
        for line in lines:
            product = DdOrderProduct(
                order_id=order_id,
                product_id=line.line.product_id,
                name=line.name,
                model=line.model,
                quantity=line.line.quantity,
                price=line.line.price,
                total=line.line.total,
            )
            self._session.add(product)
            await self._session.flush()  # to get order_product_id
            if line.option is not None:
                self._session.add(
                    DdOrderOption(
                        order_id=order_id,
                        order_product_id=product.order_product_id,
                        product_option_id=line.option.product_option_id,
                        product_option_value_id=line.option.product_option_value_id,
                        name=line.option.option_name,
                        value=line.option.value,
                        type="radio",  # as the size rows the site writes
                    )
                )

        totals = [("sub_total", "Сума", draft.sub_total), ("shipping", shipping_method, draft.shipping_cost)]
        if draft.discount > 0:
            totals.append(("coupon", "Знижка", -draft.discount))
        totals.append(("total", "Всього", draft.total))
        for sort, (code, title, value) in enumerate(totals):
            self._session.add(DdOrderTotal(order_id=order_id, code=code, title=title, value=value, sort_order=sort))
        await self._session.commit()

    def _description_of(self, product_id):
        return and_(
            DdProductDescription.product_id == product_id, DdProductDescription.language_id == self._language_id
        )
