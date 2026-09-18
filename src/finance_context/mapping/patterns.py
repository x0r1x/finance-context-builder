from __future__ import annotations

from finance_context.mapping.models import PatternWhen
from finance_context.mapping.normalize import normalize_label


def pattern_matches(
    when: PatternWhen,
    *,
    label: str,
    tokens: set[str],
    section: set[str],
) -> bool:
    if when.unless and pattern_matches(when.unless, label=label, tokens=tokens, section=section):
        return False
    checks: list[bool] = []
    if when.label_contains:
        checks.append(any(part in label for part in when.label_contains))
    if when.label_in:
        allowed = {normalize_label(item) for item in when.label_in}
        checks.append(label in allowed or label in when.label_in)
    if when.label_tokens:
        checks.append(set(when.label_tokens) <= tokens)
    if when.label_excludes:
        checks.append(not any(part in label for part in when.label_excludes))
    if when.section_contains:
        checks.append(
            any(
                set(token.split()) <= section if " " in token else token in section
                for token in when.section_contains
            )
        )
    if when.any:
        checks.append(
            any(
                pattern_matches(item, label=label, tokens=tokens, section=section)
                for item in when.any
            )
        )
    return all(checks) if checks else True
