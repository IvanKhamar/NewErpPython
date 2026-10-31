"""Shop catalog rules: the product order by sales (the old site's "Порядок товарів") and what the ERP may change in a
product."""

from collections.abc import Iterable, Mapping, Sequence, Set
from dataclasses import dataclass
from decimal import Decimal
from enum import IntEnum

from newerp.domain.common import DomainError


class SortGroupType(IntEnum):
    """A group of products the order is counted within. JSON carries the number, like a .NET enum."""

    TAG = 0  # a filter of the "themes" group; key = the filter name
    CATEGORY = 1  # a category without colors; key = the category id
    CATEGORY_COLOR = 2  # a category in one color; key = "categoryId_filterId"
    LABEL = 3  # a label ("Новинка", "Хіт"); key = the label name
    CROSS_SELL = 4  # the cross-sell panel at checkout; key = "panel". Its order is kept apart from sort_order


# The .NET names, which the query string may carry instead of the number (?type=Category).
TYPE_NAMES = {"Tag": 0, "Category": 1, "CategoryColor": 2, "Label": 3, "CrossSell": 4}

# group_type in the old site's pin table.
_TYPE_CODES = {
    SortGroupType.TAG: "tag",
    SortGroupType.CATEGORY: "category",
    SortGroupType.CATEGORY_COLOR: "category_color",
    SortGroupType.LABEL: "label",
    SortGroupType.CROSS_SELL: "crosssell",
}
CROSS_SELL_KEY = "panel"


def type_to_code(group_type: SortGroupType) -> str:
    return _TYPE_CODES[group_type]


def code_to_type(code: str) -> SortGroupType:
    for group_type, type_code in _TYPE_CODES.items():
        if type_code == code:
            return group_type
    raise DomainError(f"Невідомий вид групи «{code}».")


@dataclass(frozen=True)
class SortGroup:
    """name is for people: "Сукні — Чорний", "Мітка: Хіт"."""

    type: SortGroupType
    key: str
    name: str
    category_id: int | None = None
    filter_id: int | None = None
    label_id: int | None = None

    @property
    def type_code(self) -> str:
        return type_to_code(self.type)


@dataclass(frozen=True)
class SortPin:
    """A product pinned by hand at a position in a group (positions count from 1)."""

    position: int
    product_id: int


def by_sales(product_ids: Iterable[int], sales: Mapping[int, int]) -> list[int]:
    """Best sellers first, unsold at the end. sorted() is stable, so ties keep the original order (as in PHP 8)."""
    unique = list(dict.fromkeys(product_ids))
    return sorted(unique, key=lambda product_id: -sales.get(product_id, 0))


def merge(auto_queue: Sequence[int], pins: Iterable[SortPin]) -> list[int]:
    """A pinned product takes its position, the rest fill the free places in queue order.

    A pin beyond the number of products ends up right after them instead of getting lost.
    """
    # As in the old code: the last pin of a position wins (the table allows one), a pinned product is not repeated.
    by_position: dict[int, int] = {}
    for pin in pins:
        if pin.position >= 1:
            by_position[pin.position] = pin.product_id
    pinned = set(by_position.values())
    queue = [product_id for product_id in auto_queue if product_id not in pinned]
    total = len(queue) + len(by_position)
    result: list[int] = []
    next_in_queue = 0
    for position in range(1, total + 1):
        if position in by_position:
            result.append(by_position[position])
        elif next_in_queue < len(queue):
            result.append(queue[next_in_queue])
            next_in_queue += 1
    result += queue[next_in_queue:]
    # The old code lost pins beyond the list (its loop ended earlier). They go to the end in position order.
    result += [by_position[position] for position in sorted(by_position) if position > total]
    return list(dict.fromkeys(result))


@dataclass(frozen=True)
class ProductChange:
    """What the ERP may change in a shop product: price, enabled, labels and categories. None keeps the value.

    Stock, order, descriptions and pictures are not changed here: production, "Порядок товарів" and the OpenCart admin
    own them. The same rules as in the Java version.
    """

    price: Decimal | None = None
    enabled: bool | None = None
    label_ids: list[int] | None = None
    category_ids: list[int] | None = None

    @staticmethod
    def of(
        price: Decimal | None,
        enabled: bool | None,
        label_ids: Iterable[int] | None,
        category_ids: Iterable[int] | None,
        known_labels: Set[int],
        known_categories: Set[int],
    ) -> "ProductChange":
        """known_categories: enabled categories plus those the product is already in (a disabled one may stay)."""
        if price is not None and (price < 0 or price.quantize(Decimal("0.01")) != price):
            raise DomainError("Ціна — не менше 0, не більше двох знаків після коми.")
        labels = list(dict.fromkeys(label_ids)) if label_ids is not None else None
        if labels is not None and not all(label in known_labels for label in labels):
            raise DomainError("Такої мітки немає.")
        categories = list(dict.fromkeys(category_ids)) if category_ids is not None else None
        if categories is not None:
            # Without a category the product disappears from the shop menu.
            if not categories:
                raise DomainError("Оберіть хоча б одну категорію.")
            if not all(category in known_categories for category in categories):
                raise DomainError("Такої категорії немає або вона вимкнена.")
        return ProductChange(price, enabled, labels, categories)
