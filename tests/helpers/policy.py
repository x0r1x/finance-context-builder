from __future__ import annotations

from finance_context.mapping.models import MappingThresholds
from finance_context.settings import Settings


def _settings() -> Settings:
    return Settings(_env_file=None)


def thresholds() -> MappingThresholds:
    return _settings().mapping_thresholds()


def default_thresholds() -> MappingThresholds:
    return Settings.default_mapping_thresholds()


def slot_wait() -> float:
    return _settings().llm_slot_wait_sec


def llm_workers() -> int:
    return _settings().llm_concurrency


def accept_min() -> float:
    return _settings().concept_accept_min


def embed_floor() -> float:
    return _settings().embed_score_min


def mint_ceiling() -> float:
    return _settings().mint_score_max
