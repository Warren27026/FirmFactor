"""Complète le CSV massif avec les champs d'avis présents dans les HTML archivés.

Depuis la racine du projet : python -m scripts.enrichir_avis_massifs
Les lignes existantes et leurs identifiants sont conservés ; l'écriture est atomique.
"""

from __future__ import annotations

import argparse
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from scripts.download_html import ROOT
from scripts.importer_pages import extraire_page, pages_entreprise
from scripts.scrape_trustpilot_massif import SUPPLEMENT_FIELDS, load_reviews, save_reviews


def csv_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def enrichir(frame: pd.DataFrame, pages_dir: Path) -> tuple[pd.DataFrame, int, int, int]:
    """Associe les champs JSON aux lignes CSV par (domaine, id_externe)."""
    keys = set(zip(frame["domaine"], frame["id_externe"]))
    found: dict[tuple[str, str], dict[str, object]] = {}
    pages = errors = 0
    for domain in frame["domaine"].unique():
        for path in pages_entreprise(pages_dir, domain):
            try:
                _, reviews = extraire_page(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                errors += 1
                print(f"[ERREUR] {path}: {exc}", flush=True)
                continue
            pages += 1
            for review in reviews:
                key = (domain, review["id_externe"])
                if key in keys:
                    metadata = found.setdefault(key, {})
                    for field in SUPPLEMENT_FIELDS:
                        if review[field] is not None and field not in metadata:
                            metadata[field] = review[field]
        print(f"[OK] {domain}", flush=True)

    result = frame.copy()
    row_keys = list(zip(result["domaine"], result["id_externe"]))
    for field in SUPPLEMENT_FIELDS:
        result[field] = [
            csv_value(found.get(key, {}).get(field)) or old
            for key, old in zip(row_keys, result[field])
        ]
    return result, len(found), pages, errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=ROOT / "data/massif/avis_massifs.csv")
    parser.add_argument("--pages-dir", type=Path, default=ROOT / "data/pages")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    frame = load_reviews(args.csv)
    if frame.empty:
        parser.error(f"Aucun avis dans {args.csv}")
    result, matched, pages, errors = enrichir(frame, args.pages_dir)
    print(f"{len(result)} avis, {matched} correspondances JSON, {pages} pages lues, {errors} erreur(s)")
    for field in SUPPLEMENT_FIELDS:
        print(f"{field}: {(result[field] != '').sum()} valeurs renseignées")
    if errors:
        print("CSV non modifié à cause des erreurs de lecture")
        return 2
    if not args.dry_run:
        save_reviews(result, args.csv)
        print(f"CSV mis à jour : {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
