"""Aligne les secteurs du CSV massif sur les valeurs FirmFactor.

Usage : python -m scripts.normaliser_secteurs_massifs
"""

from __future__ import annotations

import argparse
import csv
import os
from collections import Counter
from pathlib import Path

from scripts.download_html import ROOT
from scripts.scrape_trustpilot_massif import SECTEURS


def normaliser(path: Path) -> tuple[int, Counter[str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or "secteur" not in reader.fieldnames:
            raise ValueError("Colonne secteur absente")
        fields = reader.fieldnames
        rows = list(reader)
    counts: Counter[str] = Counter()
    for row in rows:
        original = row["secteur"]
        if original not in SECTEURS:
            raise ValueError(f"Secteur inconnu : {original!r}")
        row["secteur"] = SECTEURS[original]
        counts[row["secteur"]] += 1

    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)
    return len(rows), counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=ROOT / "data/massif/avis_massifs.csv")
    args = parser.parse_args()
    total, counts = normaliser(args.csv)
    print(f"{total} avis : " + ", ".join(f"{name}={counts[name]}" for name in (
        "commerce_en_ligne", "voyages", "livraison_colis"
    )))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
