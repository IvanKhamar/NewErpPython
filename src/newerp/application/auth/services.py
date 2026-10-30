from newerp.application.auth.models import (
    AuthenticatedUser,
    ChangePasswordRequest,
    CreateUserRequest,
    CurrentUser,
    LoginRequest,
    NewUser,
    ResetPasswordRequest,
    SetAccessRequest,
    UserAccessResult,
    UserDto,
)
from newerp.application.auth.policy import Roles, generate_password, require_password
from newerp.application.auth.ports import PasswordHasher, UserLoginStore
from newerp.application.common import Clock, NotFoundError
from newerp.domain.common import DomainError


class AuthService:
    """Login and changing one's own password."""

    def __init__(self, store: UserLoginStore, hasher: PasswordHasher, clock: Clock) -> None:
        self._store = store
        self._hasher = hasher
        self._clock = clock

    async def login(self, request: LoginRequest) -> AuthenticatedUser | None:
        """
        The user if the username and password are right and the account is active, else None.
        The reason is never told, so the answer does not reveal which usernames exist.
        """
        if not request.username or not request.username.strip() or not request.password:
            return None

        user = await self._store.find_by_username(request.username.strip())
        if user is None:
            # Hash anyway: otherwise the faster answer would tell which usernames exist.
            self._hasher.hash(request.password)
            return None

        # The password is checked for a disabled user too, for the same reason.
        if not self._hasher.verify(user.password_hash, request.password) or not user.is_active:
            return None

        await self._store.set_last_login(user.user_id, self._clock.now())
        return AuthenticatedUser(
            CurrentUser(
                user_id=user.user_id,
                username=user.username,
                full_name=user.full_name,
                role=user.role,
                must_change_password=user.must_change_password,
            ),
            user.security_stamp,
        )

    async def change_password(self, user_id: int, request: ChangePasswordRequest) -> AuthenticatedUser:
        """Needs the current password. The new account version signs out all other sessions of this user."""
        user = await self._store.find_by_id(user_id)
        if user is None:
            raise DomainError("Обліковий запис не знайдено.")
        if not self._hasher.verify(user.password_hash, request.current_password or ""):
            raise DomainError("Поточний пароль неправильний.")
        if request.new_password == request.current_password:
            raise DomainError("Новий пароль має відрізнятися від поточного.")
        require_password(request.new_password, user.username)

        stamp = await self._store.save_login(
            user_id, role=None, password_hash=self._hasher.hash(request.new_password or ""), must_change_password=False
        )
        return AuthenticatedUser(
            CurrentUser(user_id=user.user_id, username=user.username, full_name=user.full_name, role=user.role), stamp
        )


class UserAdminService:
    """
    User management (Admin only): new employees, NewErp access, roles, password resets.
    Every change issues a new account version, so the person's old sessions stop working at once.
    """

    def __init__(self, store: UserLoginStore, hasher: PasswordHasher, clock: Clock) -> None:
        self._store = store
        self._hasher = hasher
        self._clock = clock

    async def list_all(self) -> list[UserDto]:
        return await self._store.list_all()

    async def create(self, request: CreateUserRequest) -> UserAccessResult:
        """A new employee: a dd_user row (shared with the old site) and a NewErp login with a temporary password."""
        username = _require_text(request.username, "Логін", 3, 20)
        user = NewUser(
            username,
            _require_text(request.first_name, "Ім'я", 1, 32),
            _require_text(request.last_name, "Прізвище", 1, 32),
            (request.email or "").strip(),
        )
        if len(user.email) > 96:
            raise DomainError("Email: не довше 96 символів.")
        role = _require_role(request.role)
        if await self._store.username_taken(username):
            raise DomainError(f'Логін "{username}" вже зайнятий.')

        password = _temporary_password(request.temporary_password, username)
        user_id = await self._store.create_user(user, self._clock.now())
        await self._store.save_login(user_id, role, self._hasher.hash(password), must_change_password=True)
        return UserAccessResult(user_id=user_id, temporary_password=password)

    async def set_access(self, actor_id: int, user_id: int, request: SetAccessRequest) -> UserAccessResult:
        """
        Gives access to an existing user or changes the role. New access needs a password (generated if missing);
        a role change keeps the password unless one is given.
        """
        user = await self._store.get(user_id)
        if user is None:
            raise NotFoundError(f"Користувача {user_id} не знайдено.")
        role = _require_role(request.role)
        if user_id == actor_id and role != Roles.ADMIN:
            raise DomainError("Не можна зняти роль Admin із себе: система може лишитися без адміністратора.")

        password = None
        if not user.has_access or request.temporary_password:
            password = _temporary_password(request.temporary_password, user.username)

        await self._store.save_login(
            user_id,
            role,
            None if password is None else self._hasher.hash(password),
            None if password is None else True,
        )
        return UserAccessResult(user_id=user_id, temporary_password=password)

    async def revoke(self, actor_id: int, user_id: int) -> None:
        """Takes NewErp access away. On the old site the user stays as they were."""
        if user_id == actor_id:
            raise DomainError("Не можна забрати доступ у самого себе.")
        if not await self._store.delete_login(user_id):
            raise NotFoundError(f"У користувача {user_id} немає доступу до NewErp.")

    async def reset_password(self, user_id: int, request: ResetPasswordRequest) -> UserAccessResult:
        """Resets the password to a temporary one; the person changes it after logging in."""
        user = await self._store.get(user_id)
        if user is None or not user.has_access:
            raise NotFoundError(f"У користувача {user_id} немає доступу до NewErp.")
        password = _temporary_password(request.temporary_password, user.username)
        await self._store.save_login(user_id, None, self._hasher.hash(password), must_change_password=True)
        return UserAccessResult(user_id=user_id, temporary_password=password)


def _temporary_password(requested: str | None, username: str) -> str:
    if not requested:
        return generate_password()
    require_password(requested, username)
    return requested


def _require_role(role: str | None) -> str:
    wanted = (role or "").strip().casefold()
    for known in Roles.ALL:
        if known.casefold() == wanted:
            return known
    raise DomainError(f"Роль має бути однією з: {', '.join(Roles.ALL)}.")


def _require_text(value: str | None, field: str, min_length: int, max_length: int) -> str:
    text = (value or "").strip()
    if not min_length <= len(text) <= max_length:
        raise DomainError(f"{field}: від {min_length} до {max_length} символів.")
    return text
