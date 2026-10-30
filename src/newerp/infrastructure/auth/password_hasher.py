"""PBKDF2 password hashes in the ASP.NET Core Identity v3 format.

The .NET and Java versions read and write the same erp_user_login.password_hash, so the format must match:
base64 of [0x01][prf: uint32][iterations: uint32][salt length: uint32][salt][subkey], numbers big-endian.
"""

import base64
import binascii
import hashlib
import hmac
import os
import struct

_FORMAT_V3 = 0x01
_PRF_NAMES = {0: "sha1", 1: "sha256", 2: "sha512"}
_PRF_SHA512 = 2
_ITERATIONS = 100_000
_SALT_SIZE = 16
_SUBKEY_SIZE = 32


class Pbkdf2PasswordHasher:
    """Salts the password and hashes it 100,000 times, so guessing passwords from a hash is very slow."""

    def __init__(self, iterations: int = _ITERATIONS) -> None:
        # The count is stored in each hash, so test data may use a cheap one and still verify here.
        self._iterations = iterations

    def hash(self, password: str) -> str:
        salt = os.urandom(_SALT_SIZE)
        subkey = hashlib.pbkdf2_hmac("sha512", password.encode(), salt, self._iterations, _SUBKEY_SIZE)
        header = struct.pack(">BIII", _FORMAT_V3, _PRF_SHA512, self._iterations, _SALT_SIZE)
        return base64.b64encode(header + salt + subkey).decode("ascii")

    def verify(self, password_hash: str, password: str) -> bool:
        try:
            blob = base64.b64decode(password_hash, validate=True)
        except (binascii.Error, ValueError):
            return False
        if len(blob) < 13 or blob[0] != _FORMAT_V3:
            return False

        prf, iterations, salt_size = struct.unpack(">III", blob[1:13])
        algorithm = _PRF_NAMES.get(prf)
        salt = blob[13 : 13 + salt_size]
        expected = blob[13 + salt_size :]
        if algorithm is None or salt_size < 16 or len(salt) != salt_size or len(expected) < 16:
            return False

        actual = hashlib.pbkdf2_hmac(algorithm, password.encode(), salt, iterations, len(expected))
        return hmac.compare_digest(actual, expected)
