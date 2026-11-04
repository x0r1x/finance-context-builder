"""One ``__pydantic_fields_set__`` per (class, field names) for row models.

Pydantic stores that set on every instance and adds the field name when an
attribute is assigned. The pipeline never reads it. Assignment copies first,
so the interned set is not mutated in place. ``model_copy`` updates a private
copy, then this interns the result.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Self

from pydantic import BaseModel

_SHARED: dict[tuple[type[BaseModel], frozenset[str]], set[str]] = {}
_SHARED_IDS: set[int] = set()


def _share_fields_set(model: BaseModel) -> None:
    current = model.__pydantic_fields_set__
    key = (type(model), frozenset(current))
    shared = _SHARED.get(key)
    if shared is None:
        shared = set(current)
        _SHARED[key] = shared
        _SHARED_IDS.add(id(shared))
    if shared is not current:
        object.__setattr__(model, "__pydantic_fields_set__", shared)


class SharedFieldsSet(BaseModel):
    def model_post_init(self, context: Any, /) -> None:
        _share_fields_set(self)

    def model_copy(
        self, *, update: Mapping[str, Any] | None = None, deep: bool = False
    ) -> Self:
        copied = super().model_copy(update=update, deep=deep)
        _share_fields_set(copied)
        return copied

    def __setattr__(self, name: str, value: Any) -> None:
        fields = self.__pydantic_fields_set__
        detached = False
        if (
            name in type(self).__pydantic_fields__
            and name not in fields
            and id(fields) in _SHARED_IDS
        ):
            object.__setattr__(self, "__pydantic_fields_set__", set(fields))
            detached = True
        try:
            super().__setattr__(name, value)
        finally:
            if detached:
                _share_fields_set(self)
