"""SMS rules shared with other modules: phone numbers and waybill numbers in a text."""

import re

from newerp.domain.common import DomainError


def normalize_phone(phone: str | None) -> str:
    """A Ukrainian mobile as 380XXXXXXXXX; "0671234567" and "+38 (067) 123-45-67" become 380671234567."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 10 and digits.startswith("0"):
        digits = "38" + digits
    if len(digits) != 12 or not digits.startswith("380"):
        raise DomainError(f'Номер "{phone}" не схожий на український мобільний.')
    return digits


def find_ttn_numbers(text: str) -> list[str]:
    """Nova Poshta waybill numbers in a text: exactly 14 digits in a row (not part of a longer number)."""
    return list(dict.fromkeys(re.findall(r"(?<!\d)\d{14}(?!\d)", text)))
