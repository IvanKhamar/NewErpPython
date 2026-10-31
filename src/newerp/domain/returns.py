"""Product returns (dd_return), like "Продажі → Повернення" of the old site: customer, product, quantity, reason,
action (money back, exchange…) and a status with its log. The old module changed neither stock nor money."""

from dataclasses import dataclass

from newerp.domain.common import DomainError

MAX_COMMENT_LENGTH = 2000


@dataclass(frozen=True)
class ReturnableLine:
    """An order line a return can be made from."""

    product_id: int
    name: str
    model: str
    quantity: int


def validate_return(
    firstname: str,
    lastname: str,
    telephone: str,
    email: str,
    order_lines: list[ReturnableLine],
    product_id: int,
    quantity: int,
    reason_id: int,
    comment: str | None,
    already_returned: int,
) -> ReturnableLine:
    """The old form's checks (names up to 32, phone 3–32, a reason), plus what it did not check: the product must be
    in the order, and at most what was bought minus earlier returns comes back.

    The e-mail is optional (the old site asked for it, though phone orders often have none), but at most 96 long.
    """
    if not 1 <= len(firstname.strip()) <= 32:
        raise DomainError("Ім'я клієнта має містити від 1 до 32 символів.")
    if len(lastname.strip()) > 32:
        raise DomainError("Прізвище клієнта має містити до 32 символів.")
    if not 3 <= len(telephone.strip()) <= 32:
        raise DomainError("Телефон клієнта має містити від 3 до 32 символів.")
    if len(email.strip()) > 96:
        raise DomainError("E-mail занадто довгий.")
    if reason_id <= 0:
        raise DomainError("Оберіть причину повернення.")
    if len(comment or "") > MAX_COMMENT_LENGTH:
        raise DomainError(f"Коментар довший за {MAX_COMMENT_LENGTH} символів.")
    lines = [line for line in order_lines if line.product_id == product_id]
    if not lines:
        raise DomainError("Цього товару немає в замовленні.")
    left = sum(line.quantity for line in lines) - already_returned
    if left <= 0:
        raise DomainError(f"«{lines[0].name}» з цього замовлення вже повернено повністю.")
    if not 0 < quantity <= left:
        raise DomainError(f"Кількість має бути від 1 до {left}.")
    return lines[0]
