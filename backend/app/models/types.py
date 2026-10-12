from typing import Any, Optional

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator

from app.security import seal, unseal


class SealedJSON(TypeDecorator):
    """JSONB column whose contents are encrypted at rest.

    Application code reads and writes plain dicts. The database only ever sees the sealed form.
    """

    impl = JSONB
    cache_ok = True

    def process_bind_param(self, value: Optional[Any], dialect):
        if value is None:
            return None
        return seal(value)

    def process_result_value(self, value: Optional[Any], dialect):
        if value is None:
            return None
        return unseal(value)
