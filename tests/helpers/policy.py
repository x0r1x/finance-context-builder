from __future__ import annotations

from finance_context.settings import Settings


def isolated_settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **overrides)
