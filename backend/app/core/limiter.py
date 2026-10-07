import hashlib

from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request


def rate_limit_key(request: Request) -> str:
    """Per-student limit on authenticated routes, per-IP otherwise (login).

    Students at a KHEL centre share one NAT address, so IP-only limiting would make a whole
    centre share a single budget. Routes resolve auth before the limiter runs, so the bearer
    token here is already verified. Only a hash is kept — never the raw token.
    """
    auth = request.headers.get("authorization", "")
    if auth[:7].lower() == "bearer " and len(auth) > 7:
        return "tok:" + hashlib.sha256(auth[7:].encode()).hexdigest()[:24]
    return get_remote_address(request)


limiter = Limiter(key_func=rate_limit_key)
