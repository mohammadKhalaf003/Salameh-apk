from slowapi import Limiter
from slowapi.util import get_remote_address
from fastapi import Request
from fastapi.responses import JSONResponse

limiter = Limiter(key_func=get_remote_address)

# KEPT from Codebase A — Codebase B had no custom handler, only the bare Limiter.
# This handler returns a clean JSON 429 response instead of the default plain-text one.
def rate_limit_exceeded_handler(request: Request, exc):
    return JSONResponse(
        status_code=429,
        content={"detail": "Too many requests. Please wait a moment before trying again."}
    )
