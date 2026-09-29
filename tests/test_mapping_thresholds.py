from __future__ import annotations

import pytest
from pydantic import ValidationError

from finance_context.mapping.induce import should_mint
from finance_context.mapping.models import Candidate, Concept, MappingThresholds, RowContext
from finance_context.mapping.resolver import Resolver
from finance_context.settings import Settings

_NAMES = (
    "CONCEPT_ACCEPT_MIN",
    "EMBED_SCORE_MIN",
    "EMBED_SCORE_GAP",
    "EMBED_TOP_K",
    "MINT_SCORE_MAX",
)


def _clear(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _NAMES:
        monkeypatch.delenv(name, raising=False)


def test_unset_thresholds_match_field_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    settings = Settings(_env_file=None)
    assert settings.mapping_thresholds() == Settings.default_mapping_thresholds()


def test_blank_thresholds_match_field_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    for name in _NAMES:
        monkeypatch.setenv(name, "  ")
    settings = Settings(_env_file=None)
    assert settings.mapping_thresholds() == Settings.default_mapping_thresholds()


def test_env_override_replaces_only_the_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("CONCEPT_ACCEPT_MIN", "0.9")
    settings = Settings(_env_file=None)
    assert settings.concept_accept_min == 0.9
    assert settings.mapping_thresholds() != Settings.default_mapping_thresholds()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("CONCEPT_ACCEPT_MIN", "8"),
        ("CONCEPT_ACCEPT_MIN", "0"),
        ("EMBED_SCORE_MIN", "1.1"),
        ("EMBED_SCORE_GAP", "-0.01"),
        ("EMBED_SCORE_GAP", "1.5"),
        ("EMBED_TOP_K", "0"),
        ("EMBED_TOP_K", "33"),
        ("MINT_SCORE_MAX", "0"),
    ],
)
def test_out_of_range_threshold_fails(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_threshold_carrier_has_no_defaults() -> None:
    with pytest.raises(ValidationError):
        MappingThresholds()


def test_threshold_stamp_roundtrip() -> None:
    current = Settings(_env_file=None).mapping_thresholds()
    loaded = MappingThresholds.model_validate(current.model_dump(mode="json"))
    assert loaded == current


def test_raised_accept_min_rejects_the_embed_floor() -> None:
    floor = Settings(_env_file=None).embed_score_min
    settings = Settings(_env_file=None, concept_accept_min=0.9)
    resolver = Resolver(
        [Concept(id="pnl.revenue", labels=["Revenue"])],
        settings.mapping_thresholds(),
    )
    ctx = RowContext(
        row_key="S|1|S!r1",
        sheet="S",
        row=1,
        block_id="S!r1",
        label="Revenue",
    )
    ranked = [
        Candidate(
            concept_id="pnl.revenue",
            score=floor,
            signal="lexical",
            evidence="label",
        )
    ]
    concept_id, picked = resolver.decide(ctx, ranked)
    assert floor < settings.concept_accept_min
    assert concept_id is None
    assert picked is None


def test_should_mint_uses_the_passed_ceiling() -> None:
    ceiling = Settings(_env_file=None).mint_score_max
    assert should_mint(0.6, ceiling) is False
    raised = Settings(_env_file=None, mint_score_max=0.7).mint_score_max
    assert should_mint(0.6, raised) is True
    assert should_mint(None, raised) is False
    assert should_mint(raised, raised) is False
