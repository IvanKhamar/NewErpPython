"""Roles and password rules."""

import secrets

from newerp.domain.common import DomainError


class Roles:
    """NewErp roles, stored in erp_user_login.role."""

    ADMIN = "Admin"
    MANAGER = "Manager"
    STOREKEEPER = "Storekeeper"
    ALL = (ADMIN, MANAGER, STOREKEEPER)


PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128
# No look-alike characters (0/O, 1/l/I), so a temporary password is easy to dictate.
_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def require_password(password: str | None, username: str) -> None:
    if not password or len(password) < PASSWORD_MIN_LENGTH:
        raise DomainError(f"Пароль має містити щонайменше {PASSWORD_MIN_LENGTH} символів.")
    if len(password) > PASSWORD_MAX_LENGTH:
        raise DomainError(f"Пароль не довший за {PASSWORD_MAX_LENGTH} символів.")
    if password.casefold() == username.casefold():
        raise DomainError("Пароль не може збігатися з логіном.")


def generate_password(length: int = 12) -> str:
    """A random password from a cryptographically strong generator."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))
