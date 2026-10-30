from newerp.infrastructure.auth.password_hasher import Pbkdf2PasswordHasher

# A hash made by ASP.NET Core Identity's PasswordHasher (v3, HMAC-SHA512, 100,000 iterations) for "test-password".
# The .NET and Java versions share erp_user_login, so this format must keep working.
DOTNET_HASH = "AQAAAAIAAYagAAAAEAy8pn5zXMB+TK+oeXx0+2W8Ud+lAeKUIik7rwnjr5u+i3tL8edzx3XJm/oDiZGABw=="


def test_verifies_hash_made_by_dotnet():
    assert Pbkdf2PasswordHasher().verify(DOTNET_HASH, "test-password")
    assert not Pbkdf2PasswordHasher().verify(DOTNET_HASH, "wrong-password")


def test_hash_has_dotnet_header():
    # Same first bytes as the .NET hash: format v3, HMAC-SHA512, 100,000 iterations, 16-byte salt.
    assert Pbkdf2PasswordHasher().hash("x")[:16] == DOTNET_HASH[:16]


def test_hash_then_verify():
    hasher = Pbkdf2PasswordHasher()
    password_hash = hasher.hash("test-password")

    assert hasher.verify(password_hash, "test-password")
    assert not hasher.verify(password_hash, "wrong-password")
    assert password_hash != hasher.hash("test-password")  # a new salt every time


def test_garbage_hash_is_rejected():
    assert not Pbkdf2PasswordHasher().verify("not base64!", "test-password")
    assert not Pbkdf2PasswordHasher().verify("", "test-password")
