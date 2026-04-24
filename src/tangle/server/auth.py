# src/tangle/server/auth.py

from __future__ import annotations

import hmac
from typing import Any

from fastapi import HTTPException, Request, status


def require_auth(request: Request) -> None:
    """FastAPI dependency. Enforces Bearer-token auth when configured.

    When `config.api_auth_token` is empty, auth is disabled (dev/sidecar mode).
    When set, requests must present `Authorization: Bearer <token>` and the
    token is compared with `hmac.compare_digest` to avoid timing leaks.
    """
    monitor: Any = request.app.state.monitor
    expected: str = monitor._config.api_auth_token
    if not expected:
        return

    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": 'Bearer realm="tangle"'},
        )
    if not hmac.compare_digest(token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid bearer token",
            headers={"WWW-Authenticate": 'Bearer realm="tangle"'},
        )
