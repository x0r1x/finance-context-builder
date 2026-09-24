from __future__ import annotations

from finance_context.app.publisher import publisher_fingerprint, publisher_matches


def test_fingerprint_is_a_stable_sha256() -> None:
    first = publisher_fingerprint()
    assert first == publisher_fingerprint()
    assert len(first) == 64
    assert first.isalnum()
    assert first == first.lower()


def test_snapshot_matches_only_the_fingerprint_that_wrote_it() -> None:
    current = publisher_fingerprint()
    assert publisher_matches({"publisher": current})
    assert not publisher_matches({"publisher": "stale"})
    assert not publisher_matches({})
    assert not publisher_matches(None)
