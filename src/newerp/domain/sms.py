"""SMS rules shared with other modules: phone numbers and waybill numbers in a text."""

import re
from datetime import datetime

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


MAX_LENGTH = 670  # SMSClub: up to 10 Cyrillic parts of 67 characters
BIRTHDAY_MARKER = 1  # pre_order_id of birthday greetings in the log (they have no order)


class SmsMessage:
    """A sent (or tried) SMS in the log (dd_sms_log of the old site), with the service's answer."""

    def __init__(self, order_id: int, phone: str, text: str, response: str, sent_at: datetime) -> None:
        self.id: int | None = None
        self.order_id = order_id
        self.pre_order_id = 0
        self.phone = phone
        self.text = text
        self.response = response
        self.sent_at = sent_at

    @staticmethod
    def birthday(phone: str, text: str, response: str, sent_at: datetime) -> "SmsMessage":
        message = SmsMessage(0, phone, text, response, sent_at)
        message.pre_order_id = BIRTHDAY_MARKER
        return message


def require_text(text: str | None) -> str:
    trimmed = (text or "").strip()
    if not trimmed:
        raise DomainError("Текст SMS порожній.")
    if len(trimmed) > MAX_LENGTH:
        raise DomainError(f"Текст SMS довший за {MAX_LENGTH} символів.")
    return trimmed
