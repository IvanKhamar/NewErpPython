from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from newerp.api.deps import get_new_order_service, get_order_queries, get_order_service
from newerp.api.errors import problem
from newerp.api.security import CurrentPrincipal, Policies, require_roles
from newerp.application.common import LookupItem, PagedResult
from newerp.application.orders.models import (
    AssignManagerRequest,
    ChangeOrderStatusRequest,
    ChangeOrderStatusResult,
    CreateOrderRequest,
    CreateOrderResult,
    CustomerPickDto,
    CustomerRiskDto,
    CustomerRiskQuery,
    OrderCompositionDto,
    OrderDetails,
    OrderFilter,
    OrderListItem,
    OrderMethodsDto,
    OrderStatusDto,
    ProductPickDto,
    UpdateOrderCompositionRequest,
    UpdateOrderCompositionResult,
)
from newerp.application.orders.services import NewOrderService, OrderService
from newerp.infrastructure.orders.queries import SqlOrderQueries

router = APIRouter(prefix="/orders", tags=["Замовлення"], dependencies=[Depends(require_roles(*Policies.ORDERS))])
Queries = Annotated[SqlOrderQueries, Depends(get_order_queries)]
Orders = Annotated[OrderService, Depends(get_order_service)]
NewOrders = Annotated[NewOrderService, Depends(get_new_order_service)]


@router.get("", summary="Список замовлень з фільтрами і пагінацією")
async def list_orders(
    queries: Queries,
    status_id: Annotated[int | None, Query(alias="statusId")] = None,
    source_id: Annotated[int | None, Query(alias="sourceId")] = None,
    manager_id: Annotated[int | None, Query(alias="managerId")] = None,
    from_: Annotated[date | None, Query(alias="from")] = None,
    to: date | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: Annotated[int, Query(alias="pageSize")] = 20,
) -> PagedResult[OrderListItem]:
    return await queries.list_orders(OrderFilter(status_id, source_id, manager_id, from_, to, search, page, page_size))


@router.get("/statuses", summary="Довідник статусів")
async def list_statuses(queries: Queries) -> list[OrderStatusDto]:
    return await queries.list_statuses()


@router.get("/managers", summary="Кого можна призначити відповідальним (активні користувачі)")
async def list_managers(queries: Queries) -> list[LookupItem]:
    return await queries.list_managers()


@router.get("/sources", summary="Довідник джерел замовлення")
async def list_sources(queries: Queries) -> list[LookupItem]:
    return await queries.list_sources()


@router.post("", status_code=201, summary="Створити замовлення вручну; risk — чи скасовував клієнт замовлення")
async def create_order(
    request: CreateOrderRequest, principal: CurrentPrincipal, service: NewOrders, response: Response
) -> CreateOrderResult:
    result = await service.create(request, principal.user_id)
    response.headers["Location"] = f"/orders/{result.order_id}"
    return result


@router.get("/new/methods", summary="Способи доставки й оплати, які вже траплялись у замовленнях")
async def list_methods(service: NewOrders) -> OrderMethodsDto:
    return await service.list_methods()


@router.get("/new/products", summary="Пошук товару за назвою, моделлю чи id: ціна й розміри")
async def search_products(service: NewOrders, search: str | None = None) -> list[ProductPickDto]:
    return await service.search_products(search)


@router.get("/new/customers", summary="Клієнти з попередніх замовлень (від 3 символів)")
async def search_customers(service: NewOrders, search: str | None = None) -> list[CustomerPickDto]:
    return await service.search_customers(search)


@router.get("/customer-risk", summary="Чи скасовував клієнт замовлення за останні 4 роки")
async def customer_risk(
    service: NewOrders,
    telephone: str | None = None,
    firstname: str | None = None,
    lastname: str | None = None,
    email: str | None = None,
) -> CustomerRiskDto:
    return await service.check_risk(CustomerRiskQuery(telephone, firstname, lastname, email))


@router.get("/{order_id}", summary="Картка замовлення: товари та історія статусів", response_model=OrderDetails)
async def get_order(order_id: int, queries: Queries) -> OrderDetails | Response:
    order = await queries.get(order_id)
    # Like Results.NotFound() in .NET: 404 with a ProblemDetails body without a detail.
    return order if order is not None else problem(404, "Not Found")


@router.put("/{order_id}/status", summary="Змінити статус (із записом у журнал); warnings — від інших модулів")
async def change_status(
    order_id: int, request: ChangeOrderStatusRequest, principal: CurrentPrincipal, service: Orders
) -> ChangeOrderStatusResult:
    return await service.change_status(order_id, request, principal.user_id)


@router.get("/{order_id}/composition", summary="Склад замовлення для редагування")
async def get_composition(order_id: int, service: NewOrders) -> OrderCompositionDto:
    return await service.get_composition(order_id)


@router.put("/{order_id}/composition", summary="Замінити склад замовлення; warnings — нестача пошитих виробів")
async def update_composition(
    order_id: int, request: UpdateOrderCompositionRequest, principal: CurrentPrincipal, service: NewOrders
) -> UpdateOrderCompositionResult:
    return await service.update_composition(order_id, request, principal.user_id)


@router.put("/{order_id}/manager", status_code=204, summary="Призначити відповідального менеджера")
async def assign_manager(
    order_id: int, request: AssignManagerRequest, principal: CurrentPrincipal, service: Orders
) -> None:
    await service.assign_manager(order_id, request, principal.user_id)
