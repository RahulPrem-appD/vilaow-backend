"""The only place that knows a business refusal has a status code.

Services raise domain errors; these handlers translate. That separation buys
three things:

  * a rule can be tested, and reused from a CLI or a worker, without HTTP
  * changing a status code never touches a rule, and adding a rule never
    touches the web layer
  * the mapping is in one table instead of scattered across 60 call sites,
    so "unpublished looks exactly like missing" is enforced once

`NotFound` deliberately covers both "does not exist" and "you may not know it
exists". An unpublished profile and a missing one must be indistinguishable
from outside or the API enumerates the pipeline, and the same is true of a
document somebody is probing ids for.
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.domain.errors import (
    Conflict,
    DomainError,
    Gone,
    Invalid,
    NotFound,
    PermissionDenied,
    Rejected,
    StorageFailure,
    TooMany,
)

# Literal codes rather than Starlette's constants: it renamed
# HTTP_422_UNPROCESSABLE_ENTITY and deprecation warnings in a table this small
# are noise. The numbers are the contract anyway.
STATUS_FOR: dict[type[DomainError], int] = {
    NotFound: 404,
    PermissionDenied: 403,
    Invalid: 422,
    Rejected: 400,
    Conflict: 409,
    Gone: 410,
    TooMany: 429,
    StorageFailure: 422,
}


def _status_for(error: DomainError) -> int:
    for kind in type(error).__mro__:
        if kind in STATUS_FOR:
            return STATUS_FOR[kind]
    return 400


def _body(error: DomainError) -> dict:
    # FastAPI's own shape: `detail`. Anything richer travels beside it rather
    # than replacing it, so existing clients keep reading the same key.
    body: dict = {"detail": error.detail}
    if isinstance(error, Invalid) and error.errors:
        body["errors"] = error.errors
    if error.context:
        body.update(error.context)
    return body


def install(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _handle(_: Request, error: DomainError) -> JSONResponse:  # noqa: RUF029
        return JSONResponse(status_code=_status_for(error), content=_body(error))
