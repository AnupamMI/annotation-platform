"""
Passwords and sessions.

- Passwords: scrypt (Python standard library), random 16-byte salt, stored as
  "scrypt$n$r$p$salt_hex$hash_hex". Comparison is constant-time.
- Sessions: a random 32-byte token in an HttpOnly cookie. Only its SHA-256 is
  stored, so a database leak doesn't hand out live sessions.
- Failed logins are rate-limited per email address (in memory, per API process).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1
COOKIE = "ap_session"
SESSION_DAYS = 14
MIN_PASSWORD = 8
MAX_FAILURES, FAILURE_WINDOW_S = 5, 300


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        # still spend the time, so response timing doesn't reveal which accounts exist
        hashlib.scrypt(b"x", salt=b"0" * 16, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
        return False
    try:
        _, n, r, p, salt, digest = stored.split("$")
        check = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=int(n), r=int(r), p=int(p), dklen=len(digest) // 2)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(check.hex(), digest)


def password_problem(password: str) -> str | None:
    if len(password) < MIN_PASSWORD:
        return f"Use at least {MIN_PASSWORD} characters."
    if password.strip() != password:
        return "The password can't start or end with a space."
    return None


def new_token() -> tuple[str, str]:
    """(token for the cookie, hash for the database)."""
    token = secrets.token_urlsafe(32)
    return token, token_hash(token)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def session_expiry() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)


class LoginLimiter:
    def __init__(self) -> None:
        self.failures: dict[str, deque[float]] = defaultdict(deque)

    def blocked(self, key: str) -> int:
        """Seconds until the next attempt is allowed (0 = allowed)."""
        q = self.failures[key]
        now = time.monotonic()
        while q and now - q[0] > FAILURE_WINDOW_S:
            q.popleft()
        return int(FAILURE_WINDOW_S - (now - q[0])) + 1 if len(q) >= MAX_FAILURES else 0

    def fail(self, key: str) -> None:
        self.failures[key].append(time.monotonic())

    def reset(self, key: str) -> None:
        self.failures.pop(key, None)


limiter = LoginLimiter()
