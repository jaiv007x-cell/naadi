#!/usr/bin/env python3
"""Seed script placeholder — wires Postgres when prod DB is ready."""

from __future__ import annotations

import json
from pathlib import Path


def main() -> None:
    corpus_path = Path(__file__).parent.parent / "services/pratibimb/app/case_gen/seed_corpus.json"
    with corpus_path.open(encoding="utf-8") as f:
        cases = json.load(f)
    print(f"Seed corpus: {len(cases)} case(s) loaded from {corpus_path}")
    print("Postgres seeding not yet implemented — MVP uses in-memory + JSON corpus.")
    for i, case in enumerate(cases):
        print(f"  [{i + 1}] {case['demographics']['name']} — {case['hidden']['primary_diagnosis']}")


if __name__ == "__main__":
    main()
