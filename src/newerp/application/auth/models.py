from dataclasses import dataclass
from datetime import datetime

from newerp.application.common import Dto


class LoginRequest(Dto):
    username: str | None = None
    password: str | None = None


class CurrentUser(Dto):
    """The signed-in user. must_change_password: the password is temporary and must be changed."""

    user_id: int
    username: str
    full_name: str
    role: str
    must_change_password: bool = False


@dataclass(frozen=True)
class AuthenticatedUser:
    """Login result: the user and the account version that goes into the cookie."""

    user: CurrentUser
    security_stamp: str


@dataclass(frozen=True)
class UserLoginRecord:
    """Account for the login check: dd_user data plus password hash and role from erp_user_login."""

    user_id: int
    username: str
    full_name: str
    is_active: bool
    password_hash: str
    role: str
    security_stamp: str
    must_change_password: bool


class ChangePasswordRequest(Dto):
    current_password: str | None = None
    new_password: str | None = None


# ── User management (Admin) ──


class UserDto(Dto):
    """has_access: may log in to NewErp. active_on_old_site is dd_user.status: disabled there means no NewErp either."""

    user_id: int
    username: str
    full_name: str
    email: str
    active_on_old_site: bool
    has_access: bool
    role: str | None
    last_login_at: datetime | None


class CreateUserRequest(Dto):
    """No temporary_password: the system generates one and returns it once."""

    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    role: str | None = None
    temporary_password: str | None = None


class SetAccessRequest(Dto):
    role: str | None = None
    temporary_password: str | None = None


class ResetPasswordRequest(Dto):
    temporary_password: str | None = None


class UserAccessResult(Dto):
    """temporary_password is shown only here, once; the person changes it after logging in."""

    user_id: int
    temporary_password: str | None


@dataclass(frozen=True)
class NewUser:
    """A new employee for dd_user."""

    username: str
    first_name: str
    last_name: str
    email: str
