"""Password hashing (argon2id) and the few rules a new password must meet."""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

MIN_LENGTH = 10
MAX_LENGTH = 256  # argon2 does not care, but nobody needs a 1 MB password

#: The most common passwords long enough to pass the length rule. Not an
#: exhaustive breach list - just the ones people actually type first.
COMMON = frozenset(
    {
        "1234567890", "12345678910", "123456789a", "1234567890a", "0123456789",
        "1q2w3e4r5t", "1qaz2wsx3edc", "qwertyuiop", "qwertyuiop1", "password12",
        "password123", "password1234", "passw0rd123", "iloveyou12", "abcdefghij",
        "abc1234567", "aaaaaaaaaa", "1111111111", "0000000000", "9876543210",
        "letmein123", "welcome123", "football12", "baseball12", "dragon1234",
        "monkey1234", "sunshine12", "princess12", "superman12", "trustno1234",
        "starwars12", "computer12", "michael123", "qazwsxedcr", "zaq12wsxcde",
        "magicthegathering", "mtgcommander", "goldfisher", "goldfisher1",
    }
)

_hasher = PasswordHasher()  # argon2id with the library's current defaults

#: A hash of nothing in particular, checked against when the email is
#: unknown, so a wrong email takes as long to reject as a wrong password.
_DUMMY = _hasher.hash("not a real password, only here for timing")


def problem(password: str, *, email: str | None = None, name: str | None = None) -> str | None:
    """Why this password will not do, or None if it is fine."""
    if len(password) < MIN_LENGTH:
        return f"use at least {MIN_LENGTH} characters"
    if len(password) > MAX_LENGTH:
        return f"use at most {MAX_LENGTH} characters"
    lowered = password.casefold()
    if lowered in COMMON or len(set(lowered)) <= 2:
        return "that password is too common - pick something less guessable"
    for part in (email.split("@")[0] if email else None, name):
        if part and len(part) >= 4 and lowered == part.casefold():
            return "don't use your name or email as the password"
    return None


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify(stored: str | None, password: str) -> bool:
    """Whether ``password`` matches ``stored``. Always spends the same work."""
    try:
        return _hasher.verify(stored or _DUMMY, password) and stored is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored: str) -> bool:
    try:
        return _hasher.check_needs_rehash(stored)
    except InvalidHashError:
        return False
