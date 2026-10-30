from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal


class DomainError(Exception):
    """A broken business rule. The API turns it into 400 (like DomainException in .NET)."""


def plain(value: Decimal, places: int | None = None) -> str:
    """A number without extra zeros: 650, 650.5 (.NET "0.##########"; with places=3 like "0.###")."""
    if places is not None:
        value = value.quantize(Decimal(1).scaleb(-places), ROUND_HALF_EVEN)
    text = format(value.normalize(), "f")
    return "0" if text in ("-0", "") else text


def round_money(value: Decimal) -> Decimal:
    """Math.Round(value, 2, MidpointRounding.AwayFromZero)."""
    return value.quantize(Decimal("0.01"), ROUND_HALF_UP)


def require_text(value: str | None, field: str, max_length: int) -> str:
    text = (value or "").strip()
    if not text:
        raise DomainError(f"{field}: значення обов'язкове.")
    if len(text) > max_length:
        raise DomainError(f"{field}: не довше {max_length} символів.")
    return text
