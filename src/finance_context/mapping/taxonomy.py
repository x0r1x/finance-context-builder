from __future__ import annotations

from pathlib import Path

import yaml

from finance_context.mapping.facets import enrich_concept
from finance_context.mapping.models import Concept

_DEFAULT = Path(__file__).resolve().parents[1] / "ontology" / "taxonomy.yaml"


def load_taxonomy(path: Path | None = None) -> list[Concept]:
    data = yaml.safe_load((path or _DEFAULT).read_text(encoding="utf-8"))
    return [enrich_concept(Concept.model_validate(item)) for item in data["concepts"]]
