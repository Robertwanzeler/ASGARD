"""Small, shared guard for local GreenRAN HTTP mutation endpoints."""

from __future__ import annotations

import hmac
import os
from functools import wraps
from typing import Any, Callable

from flask import jsonify, request


def _is_loopback(address: str | None) -> bool:
    return address in {None, "127.0.0.1", "::1", "localhost"}


def require_mutation_token(function: Callable[..., Any]) -> Callable[..., Any]:
    """Require an environment token for mutating requests.

    Local development remains usable without a token, but non-loopback access is
    denied unless GREENRAN_API_TOKEN is configured explicitly.
    """

    @wraps(function)
    def guarded(*args: Any, **kwargs: Any) -> Any:
        configured = os.environ.get("GREENRAN_API_TOKEN", "")
        provided = request.headers.get("Authorization", "")
        if provided.lower().startswith("bearer "):
            provided = provided[7:].strip()
        if not configured:
            if not _is_loopback(request.remote_addr):
                return jsonify({"error": "GREENRAN_API_TOKEN is required"}), 503
        elif not hmac.compare_digest(provided, configured):
            return jsonify({"error": "invalid API token"}), 401
        return function(*args, **kwargs)

    return guarded
