"""Télécharge les pages autorisées du CSV et extrait leurs avis.

Depuis la racine du projet :
    python -m scripts.scrape_trustpilot_csv --max-entreprises 1

Le script vérifie robots.txt avant chaque page et s'arrête sur un refus HTTP.
Il ne contourne pas les restrictions du site.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx
from scripts.download_html import page_path_for
from scripts.scrape_trustpilot import _next_url, parse_page


ROOT = Path(__file__).resolve().parents[1]
USER_AGENT = "FirmFactorAcademicResearch/0.1"
FIELDS = (
    "entreprise", "domaine", "id_externe", "note", "titre", "texte",
    "date_publication", "date_experience", "verifie", "reponse_texte",
    "reponse_date", "auteur_nb_avis",
)


class CollectionStopped(Exception):
    """Le serveur refuse la collecte ; ne pas poursuivre les entreprises."""


def is_trustpilot_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and (
        parsed.hostname == "trustpilot.com"
        or (parsed.hostname or "").endswith(".trustpilot.com")
    )


def read_companies(path: Path, maximum: int | None) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as file:
        companies = list(csv.DictReader(file))
    if maximum is not None:
        companies = companies[:maximum]
    for company in companies:
        if not is_trustpilot_url(company.get("url_trustpilot", "")):
            raise ValueError(f"URL Trustpilot invalide : {company.get('url_trustpilot')}")
    return companies


def robots_for(client: httpx.Client, url: str) -> RobotFileParser:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    response = client.get(robots_url)
    response.raise_for_status()
    parser = RobotFileParser()
    parser.parse(response.text.splitlines())
    return parser


def collect_company(
    client: httpx.Client,
    company: dict[str, str],
    output_dir: Path,
    robots: RobotFileParser,
    limit: int,
    max_pages: int,
    delay: float,
    pages_dir: Path | None = None,
) -> list[dict]:
    url = company["url_trustpilot"]
    items: list[dict] = []
    seen: set[str] = set()
    pages_dir = pages_dir or ROOT / "data/pages"

    for page_number in range(1, max_pages + 1):
        if not robots.can_fetch(USER_AGENT, url):
            print(f"Ignoré par robots.txt : {url}")
            break

        response = client.get(url)
        if response.status_code in (403, 429):
            raise CollectionStopped(f"HTTP {response.status_code} sur {url}")
        response.raise_for_status()
        if not is_trustpilot_url(str(response.url)):
            raise ValueError(f"Redirection hors de Trustpilot : {response.url}")

        path = page_path_for(company, pages_dir, page_number)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(response.text, encoding="utf-8")

        soup, reviews = parse_page(response.text, company["nom"], company["domaine"])
        for item in reviews:
            if item["id_externe"] not in seen:
                seen.add(item["id_externe"])
                items.append(item)
                if len(items) >= limit:
                    break
        next_url = _next_url(soup, url, company["url_trustpilot"], page_number + 1)
        if not next_url or len(items) >= limit:
            break
        url = next_url
        if delay:
            time.sleep(delay)

    return items


def write_exports(items: list[dict], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "trustpilot.json").write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (output_dir / "trustpilot.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(items)
    with (output_dir / "trustpilot.jsonl").open("w", encoding="utf-8") as file:
        for item in items:
            file.write(json.dumps(item, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=ROOT / "scraping/entreprises.csv")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "exports")
    parser.add_argument("--max-entreprises", type=int)
    parser.add_argument("--limit-par-entreprise", type=int, default=20)
    parser.add_argument("--max-pages", type=int, default=10)
    parser.add_argument("--delay", type=float, default=2.0)
    args = parser.parse_args()
    if (args.max_entreprises is not None and args.max_entreprises < 1) or any(
        value <= 0 for value in (args.limit_par_entreprise, args.max_pages)
    ) or args.delay < 0:
        parser.error("Les limites doivent être positives et le délai non négatif")

    companies = read_companies(args.csv, args.max_entreprises)
    items: list[dict] = []
    robots_cache: dict[str, RobotFileParser] = {}
    with httpx.Client(
        headers={"User-Agent": USER_AGENT}, timeout=20, follow_redirects=False
    ) as client:
        for company in companies:
            url = company["url_trustpilot"]
            origin = urlparse(url).netloc
            if origin not in robots_cache:
                try:
                    robots_cache[origin] = robots_for(client, url)
                except httpx.HTTPError as exc:
                    print(f"robots.txt inaccessible pour {origin} : {exc}")
                    return 2
            if not robots_cache[origin].can_fetch(USER_AGENT, url):
                print(f"Collecte arrêtée : robots.txt interdit {url}")
                return 2
            print(f"Collecte : {company['nom']}")
            try:
                items.extend(collect_company(
                    client, company, args.output_dir, robots_cache[origin],
                    args.limit_par_entreprise, args.max_pages, args.delay,
                ))
            except (httpx.HTTPError, CollectionStopped) as exc:
                print(f"Collecte arrêtée pour {company['nom']} : {exc}")
                return 2
            if args.delay:
                time.sleep(args.delay)

    write_exports(items, args.output_dir)
    print(f"{len(items)} avis exportés dans {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
