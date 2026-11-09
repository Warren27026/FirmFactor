"""Déplace les HTML historiques de data/html vers data/pages/<domaine>/pageNNNN.html."""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

from scripts.download_html import ROOT, html_path_for
from scripts.scrape_trustpilot_massif import page_path, read_targets


def migrer(csv_path: Path, ancien: Path, nouveau: Path) -> int:
    cibles = read_targets(csv_path)
    deplaces = 0
    for source in sorted(ancien.glob("*.html")):
        match = re.fullmatch(r"(\d{3})-.+?(?:-page-(\d+))?\.html", source.name)
        if match is None:
            raise ValueError(f"Nom de page historique inattendu : {source}")
        index = int(match.group(1))
        page = int(match.group(2) or 1)
        if not 1 <= index <= len(cibles):
            raise ValueError(f"Index d'entreprise inattendu : {source}")
        cible = cibles[index - 1]
        if source != html_path_for(cible, index, ancien, page):
            raise ValueError(f"Nom de page incohérent avec entreprises.csv : {source}")
        destination = page_path(nouveau, cible["domaine"], page)
        if destination.exists():
            raise FileExistsError(f"Destination déjà présente : {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, destination)
        deplaces += 1
    return deplaces


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=ROOT / "scraping/entreprises.csv")
    parser.add_argument("--ancien", type=Path, default=ROOT / "data/html")
    parser.add_argument("--nouveau", type=Path, default=ROOT / "data/pages")
    args = parser.parse_args()
    print(f"{migrer(args.csv, args.ancien, args.nouveau)} pages déplacées")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
