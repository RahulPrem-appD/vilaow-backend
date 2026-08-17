"""Where uploaded bytes live, as an interface.

This one was already close to right — storage.py had an ABC and two backends —
so the change is mostly relocation: the protocol belongs with the other ports,
and the validation rules that sat beside it belong in the service that applies
them, not in the thing that writes bytes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Stored:
    path: str
    content_type: str
    size_bytes: int


class StorageBackend(Protocol):
    """Put bytes somewhere durable and get them back.

    Implementations must not decide *whether* a file is allowed — that is a
    domain rule about photos and documents, and it lives in the service. A
    backend's only job is bytes in, bytes out.
    """

    def put(self, data: bytes, *, key: str, content_type: str) -> Stored: ...

    def get(self, path: str) -> bytes: ...

    def delete(self, path: str) -> None: ...
