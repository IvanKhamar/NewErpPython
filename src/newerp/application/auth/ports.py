"""Interfaces the auth services need; infrastructure implements them (like IUserLoginStore in .NET)."""

from datetime import datetime
from typing import Protocol

from newerp.application.auth.models import NewUser, UserDto, UserLoginRecord


class PasswordHasher(Protocol):
    """Only the hash of a password is stored, never the password itself."""

    def hash(self, password: str) -> str: ...

    def verify(self, password_hash: str, password: str) -> bool: ...


class UserLoginStore(Protocol):
    """Users of the old site (dd_user) and their NewErp login (erp_user_login)."""

    async def find_by_username(self, username: str) -> UserLoginRecord | None:
        """None if there is no such username or the user has no NewErp login."""
        ...

    async def find_by_id(self, user_id: int) -> UserLoginRecord | None: ...

    async def set_last_login(self, user_id: int, logged_in_at: datetime) -> None: ...

    async def get_active_security_stamp(self, user_id: int) -> str | None:
        """The account version if the login exists and the user is active on the old site, else None."""
        ...

    async def list_all(self) -> list[UserDto]: ...

    async def get(self, user_id: int) -> UserDto | None: ...

    async def username_taken(self, username: str) -> bool: ...

    async def create_user(self, user: NewUser, now: datetime) -> int:
        """Adds an employee to dd_user (without an old-site password, so the old admin stays closed to them)."""
        ...

    async def save_login(
        self, user_id: int, role: str | None, password_hash: str | None, must_change_password: bool | None
    ) -> str:
        """Creates or changes the login; None means "keep". Always issues and returns a new account version."""
        ...

    async def delete_login(self, user_id: int) -> bool:
        """False if there was no login."""
        ...
