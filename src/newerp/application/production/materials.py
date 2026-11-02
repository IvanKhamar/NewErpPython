"""Consumables for the sewing plan (PlanMaterialsService in .NET): how much is needed to cut and sew what active orders
lack, and what is missing even after the stock and what is ordered from suppliers.

scope "minimum" (Python first, plan item 27) counts sewing every model up to its minimal stock instead: the pieces it
lacks after the orders are spread over its sizes by their shares in a batch; "all" counts both. The old module counted
none of this: a shortage showed only when a cut or sewn operation wrote off "what there is"."""

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from newerp.application.common import ukrainian_key
from newerp.application.production.models import PlanMaterialDto, PlanMaterialsDto, PlanMaterialUseDto
from newerp.application.production.ports import ProductionModelFilter, ProductionRepository
from newerp.application.production.services import SewingPlanService
from newerp.application.stock.ports import PurchaseOrderRepository
from newerp.domain.production import MaterialNeed, material_needs, spread_by_share

READY_MADE = "ready_made"  # bought from a supplier, not sewn: no minimum to sew


class MaterialScope(StrEnum):
    ORDERS = "orders"
    MINIMUM = "minimum"
    ALL = "all"


@dataclass(frozen=True)
class _SizeTask:
    production_id: int
    model_name: str
    size_id: int
    size_name: str
    to_cut: Decimal
    to_sew: Decimal
    for_minimum: bool


class PlanMaterialsService:
    def __init__(
        self, plan: SewingPlanService, repository: ProductionRepository, purchase_orders: PurchaseOrderRepository
    ) -> None:
        self._plan = plan
        self._repository = repository
        self._purchase_orders = purchase_orders

    async def get(self, scope: MaterialScope = MaterialScope.ORDERS) -> PlanMaterialsDto:
        rows = (await self._plan.get(only_to_sew=False)).rows
        tasks: list[_SizeTask] = []
        if scope in (MaterialScope.ORDERS, MaterialScope.ALL):
            tasks += [
                _SizeTask(r.production_id, r.model_name, r.size_id, r.size_name, r.to_cut, r.to_sew, False)
                for r in rows
                if r.to_sew > 0
            ]
        if scope in (MaterialScope.MINIMUM, MaterialScope.ALL):
            tasks += await self._up_to_minimum({r.size_id: r for r in rows})

        needs: list[tuple[MaterialNeed, _SizeTask]] = []
        by_model: dict[int, list[_SizeTask]] = defaultdict(list)
        for task in tasks:
            by_model[task.production_id].append(task)
        for production_id, model_tasks in by_model.items():
            model = await self._repository.find_model(production_id)
            if model is None:
                continue
            components = await self._repository.find_components(production_id)
            for task in model_tasks:
                needs += [(n, task) for n in material_needs(model, components, task.size_id, task.to_cut, task.to_sew)]

        item_ids = list(dict.fromkeys(need.item_id for need, _ in needs))
        items = {i.id: i for i in await self._repository.find_stock_items(item_ids)}
        on_order = await self._purchase_orders.on_order()
        warnings = list(
            dict.fromkeys(
                f"Розхідника {need.item_id} зі специфікації моделі «{task.model_name}» немає на складі, "
                "його не враховано."
                for need, task in needs
                if need.item_id not in items
            )
        )

        grouped: dict[int, list[tuple[MaterialNeed, _SizeTask]]] = defaultdict(list)
        for need, task in needs:
            if need.item_id in items:
                grouped[need.item_id].append((need, task))
        materials = []
        for item_id, item_needs in grouped.items():
            item = items[item_id]
            needed = sum((need.quantity for need, _ in item_needs), Decimal(0))
            ordered = on_order.get(item_id, Decimal(0))
            uses: dict[tuple[int, str, str, str, bool], Decimal] = defaultdict(Decimal)
            for need, task in item_needs:
                uses[(task.production_id, task.model_name, task.size_name, need.stage, task.for_minimum)] += (
                    need.quantity
                )
            materials.append(
                PlanMaterialDto(
                    item_id=item_id,
                    name=item.name,
                    unit=item.unit,
                    needed=needed,
                    in_stock=item.quantity,
                    on_order=ordered,
                    shortage=max(Decimal(0), needed - max(Decimal(0), item.quantity) - ordered),
                    uses=[
                        PlanMaterialUseDto(
                            production_id=pid,
                            model_name=name,
                            size_name=size,
                            stage=stage,
                            quantity=quantity,
                            for_minimum=minimum,
                        )
                        for (pid, name, size, stage, minimum), quantity in uses.items()
                    ],
                )  # fmt: skip
            )
        materials.sort(key=lambda m: (m.shortage <= 0, ukrainian_key(m.name)))
        return PlanMaterialsDto(materials=materials, warnings=warnings)

    async def _up_to_minimum(self, plan_rows: dict) -> list[_SizeTask]:
        """What each sewn model lacks to its minimal stock once the orders took theirs, per size."""
        tasks = []
        for summary in await self._repository.list_models(ProductionModelFilter()):
            if summary.type == READY_MADE or summary.min_total <= 0:
                continue
            model = await self._repository.find_model(summary.id)
            if model is None:
                continue
            sewn_after, cut_after = {}, {}
            for size in model.sizes:
                row = plan_rows.get(size.id)
                # Orders take from the sewn stock what they lack minus what still has to be sewn for them.
                taken = row.missing - row.to_sew if row else Decimal(0)
                sewn_after[size.id] = max(Decimal(0), size.sewn_stock - taken)
                cut_after[size.id] = max(Decimal(0), size.cut_stock - (row.from_cut if row else Decimal(0)))
            lacking = Decimal(model.min_total) - sum(sewn_after.values(), Decimal(0))
            pieces = spread_by_share(lacking, {s.id: s.percent for s in model.sizes})
            for size in model.sizes:
                to_sew = Decimal(pieces.get(size.id, 0))
                if to_sew > 0:
                    to_cut = max(Decimal(0), to_sew - cut_after[size.id])
                    tasks.append(_SizeTask(model.id, model.name, size.id, size.name, to_cut, to_sew, True))
        return tasks
