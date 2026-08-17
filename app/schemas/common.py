"""Shared base for every wire shape.

Kept apart from the models on purpose: those are the storage shape and carry
password hashes and internal foreign keys; these are what leaves the server.
`from_attributes` lets a schema be built straight off an ORM instance.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)
