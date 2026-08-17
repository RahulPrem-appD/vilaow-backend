"""Wire shapes, one module per aggregate.

This was a single 520-line module. Splitting it means a change to the
introduction schemas cannot touch the agreement ones, and each file names what
it is for. Everything is re-exported here so `from app.schemas import X` keeps
working for the routers and for the tests.
"""
from __future__ import annotations

from app.schemas.common import ORMModel  # noqa: F401
from app.schemas.staff import *  # noqa: F401,F403
from app.schemas.professions import *  # noqa: F401,F403
from app.schemas.profession_fields import *  # noqa: F401,F403
from app.schemas.reviews import *  # noqa: F401,F403
from app.schemas.professionals import *  # noqa: F401,F403
from app.schemas.imports import *  # noqa: F401,F403
from app.schemas.leads import *  # noqa: F401,F403
from app.schemas.agreements import *  # noqa: F401,F403
from app.schemas.introductions import *  # noqa: F401,F403
from app.schemas.verified_reviews import *  # noqa: F401,F403
from app.schemas.assets import *  # noqa: F401,F403
