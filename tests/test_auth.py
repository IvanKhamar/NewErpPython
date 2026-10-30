import pytest

from newerp.api.deps import get_clock, get_login_throttle
from newerp.application.auth.throttle import LoginThrottle, LoginThrottleOptions
from newerp.main import app
from tests.seed import ADMIN, INACTIVE, MANAGER, MANAGER_USER_ID, PASSWORD


def test_login_then_me_returns_current_user(api):
    me = api(MANAGER).get("/auth/me")

    assert me.json() == {
        "userId": MANAGER_USER_ID,
        "username": MANAGER,
        "fullName": "Марта Тест",
        "role": "Manager",
        "mustChangePassword": False,
    }


@pytest.mark.parametrize(
    ("username", "password"),
    [
        (ADMIN, "wrong-password"),
        (INACTIVE, PASSWORD),  # a disabled user cannot log in
        ("nobody", PASSWORD),
    ],
)
def test_login_rejected_returns_401(api, username, password):
    response = api().post("/auth/login", json={"username": username, "password": password})

    assert response.status_code == 401
    assert response.json()["title"] == "Невірний логін або пароль"


def test_anonymous_returns_401(api):
    assert api().get("/auth/me").status_code == 401
    assert api().get("/users").status_code == 401


def test_logout_ends_session(api):
    client = api(MANAGER)

    assert client.post("/auth/logout").status_code == 204
    assert client.get("/auth/me").status_code == 401


def test_tampered_cookie_returns_401(api):
    client = api(MANAGER)
    tampered = client.cookies["newerp.auth"][:-2] + "xx"
    client.cookies.clear()
    client.cookies.set("newerp.auth", tampered)

    assert client.get("/auth/me").status_code == 401


def test_broken_json_returns_400_problem(api):
    response = api().post("/auth/login", content=b"{not json", headers={"Content-Type": "application/json"})

    assert response.status_code == 400
    assert response.json()["title"] == "Некоректний запит"


def test_every_response_has_security_headers(api):
    response = api().get("/health")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_too_many_failures_returns_429_even_with_right_password(api):
    throttle = LoginThrottle(LoginThrottleOptions(max_failures_per_user=3), get_clock())
    app.dependency_overrides[get_login_throttle] = lambda: throttle
    try:
        client = api()
        for _ in range(3):
            response = client.post("/auth/login", json={"username": MANAGER, "password": "wrong-password"})
            assert response.status_code == 401

        blocked = client.post("/auth/login", json={"username": MANAGER, "password": PASSWORD})
        assert blocked.status_code == 429
        assert int(blocked.headers["Retry-After"]) > 0
        assert blocked.json()["title"] == "Забагато невдалих спроб входу"

        other = client.post("/auth/login", json={"username": ADMIN, "password": PASSWORD})
        assert other.status_code == 200
    finally:
        app.dependency_overrides.pop(get_login_throttle)
