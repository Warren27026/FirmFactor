"""Exporte hors ligne les avis des pages enregistrées dans data/pages/."""

from __future__ import annotations

import argparse
from pathlib import Path

from scripts.download_html import ROOT, page_path_for, read_companies
from scripts.scrape_trustpilot import collect
from scripts.scrape_trustpilot_csv import write_exports


def parse_saved_pages(
    companies: list[dict[str, str]],
    html_dir: Path,
    limit_per_company: int,
    max_pages: int = 1,
) -> list[dict]:
    items: list[dict] = []
    for company in companies:
        first = page_path_for(company, html_dir)
        if not first.is_file():
            raise FileNotFoundError(f"HTML manquant : {first}")
        reviews = collect(
            company["url_trustpilot"], company["nom"], company["domaine"],
            limit_per_company, max_pages, html_dir, offline_only=True,
        )
        items.extend(reviews)
        print(f"{company['nom']} : {len(reviews)} avis")
    return items


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=ROOT / "scraping/entreprises.csv")
    parser.add_argument("--html-dir", type=Path, default=ROOT / "data/pages")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/parsed")
    parser.add_argument("--max-entreprises", type=int)
    parser.add_argument("--max-pages", type=int, default=1)
    parser.add_argument("--limit-par-entreprise", type=int, default=100)
    args = parser.parse_args()
    if args.max_entreprises is not None and args.max_entreprises < 1:
        parser.error("--max-entreprises doit être positif")
    if args.limit_par_entreprise < 1 or args.max_pages < 1:
        parser.error("Les limites doivent être positives")
    try:
        companies = read_companies(args.csv, args.max_entreprises)
        items = parse_saved_pages(
            companies, args.html_dir, args.limit_par_entreprise, args.max_pages
        )
        write_exports(items, args.output_dir)
    except (OSError, ValueError) as exc:
        print(f"Arrêt : {exc}")
        return 2
    print(f"{len(items)} avis exportés dans {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
