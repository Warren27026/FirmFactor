"""Collecte ciblée Trustpilot avec Chrome headless et BeautifulSoup.

Depuis la racine du projet :
    python -m scripts.scrape_trustpilot
    python -m scripts.scrape_trustpilot --limit 100 --max-pages 6

Les pages HTML sont conservées dans data/pages/. Les exports JSON, CSV et JSONL
sont des fichiers locaux ; l'import Supabase utilise scripts.importer_pages.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import random
import time
from datetime import date, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup
from selenium.common.exceptions import WebDriverException

from scripts.download_html import ROOT, is_trustpilot_url, page_path_for
from scripts.importer_pages import extraire_avis
from scripts.scrape_trustpilot_massif import fetch_html, next_page_url, parse_articles

LOG = logging.getLogger("scrape_trustpilot")
FIELDS = (
    "entreprise", "domaine", "id_externe", "note", "titre", "texte",
    "date_publication", "date_experience", "verifie", "reponse_texte",
    "reponse_date", "auteur_nb_avis",
)


def _export_value(value: object) -> object:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def parse_page(html: str, entreprise: str, domaine: str) -> tuple[BeautifulSoup, list[dict]]:
    """Lit __NEXT_DATA__ avec BeautifulSoup, puis les cartes HTML en repli."""
    soup = BeautifulSoup(html, "html.parser")
    script = soup.select_one("script#__NEXT_DATA__")
    reviews: list[dict] = []
    if script is not None and script.string:
        try:
            raw_reviews = json.loads(script.string)["props"]["pageProps"]["reviews"]
            if not isinstance(raw_reviews, list):
                raise ValueError("reviews n'est pas une liste")
            for raw in raw_reviews:
                try:
                    mapped = extraire_avis(raw)
                except (ValueError, TypeError, KeyError) as exc:
                    LOG.warning("Avis JSON ignoré : %s", exc)
                    continue
                reviews.append({
                    "entreprise": entreprise,
                    "domaine": domaine,
                    **{key: _export_value(value) for key, value in mapped.items()},
                })
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            LOG.warning("__NEXT_DATA__ inexploitable : %s", exc)
    if not reviews:
        for item in parse_articles(soup):
            reviews.append({
                "entreprise": entreprise, "domaine": domaine,
                "id_externe": item["id_externe"], "note": item["note"],
                "titre": item["titre"], "texte": item["texte"],
                "date_publication": item["date_publication"] or None,
                "date_experience": None, "verifie": None,
                "reponse_texte": None, "reponse_date": None,
                "auteur_nb_avis": None,
            })
    return soup, reviews


def _next_url(soup: BeautifulSoup, current_url: str, base_url: str, following_page: int) -> str | None:
    url = next_page_url(soup, current_url, base_url, following_page)
    if url:
        return url
    script = soup.select_one("script#__NEXT_DATA__")
    if script is None or not script.string:
        return None
    try:
        props = json.loads(script.string)["props"]["pageProps"]
        total_pages = int(props["filters"]["pagination"]["totalPages"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if following_page > total_pages:
        return None
    parsed = urlparse(current_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["page"] = str(following_page)
    return urlunparse(parsed._replace(query=urlencode(query), fragment=""))


def collect(
    url: str, entreprise: str, domaine: str, limit: int, max_pages: int,
    pages_dir: Path, chrome_binary: Path | None = None, offline_only: bool = False,
) -> list[dict]:
    if not is_trustpilot_url(url) or not urlparse(url).path.startswith("/review/"):
        raise ValueError("URL Trustpilot invalide")
    seen: set[str] = set()
    items: list[dict] = []
    current_url = url
    for page in range(1, max_pages + 1):
        path = page_path_for({"domaine": domaine}, pages_dir, page)
        downloaded = False
        if path.is_file():
            html = path.read_text(encoding="utf-8")
        else:
            if offline_only:
                break
            html = fetch_html(current_url, url, page, chrome_binary)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(html, encoding="utf-8")
            downloaded = True
            LOG.info("Page enregistrée : %s", path)
        soup, reviews = parse_page(html, entreprise, domaine)
        if not reviews:
            raise ValueError(f"Aucun avis extrait de {path}")
        for item in reviews:
            review_id = str(item["id_externe"])
            if review_id not in seen:
                seen.add(review_id)
                items.append(item)
                if len(items) >= limit:
                    return items
        LOG.info("Page %s : %s avis uniques au total", page, len(items))
        following = _next_url(soup, current_url, url, page + 1)
        if following is None:
            break
        current_url = following
        if downloaded and page < max_pages:
            time.sleep(random.uniform(3.0, 5.0))
    return items


def write_exports(items: list[dict], output_dir: Path, prefix: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / f"{prefix}.json").write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with (output_dir / f"{prefix}.jsonl").open("w", encoding="utf-8") as file:
        for item in items:
            file.write(json.dumps(item, ensure_ascii=False) + "\n")
    with (output_dir / f"{prefix}.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(items)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="https://fr.trustpilot.com/review/www.cdiscount.com?sort=recency")
    parser.add_argument("--entreprise", default="Cdiscount")
    parser.add_argument("--domaine", default="www.cdiscount.com")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--max-pages", type=int, default=50)
    parser.add_argument("--pages-dir", type=Path, default=ROOT / "data/pages")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "exports")
    parser.add_argument("--prefix", default="cdiscount")
    parser.add_argument("--chrome-binary", type=Path)
    parser.add_argument("--offline-only", action="store_true")
    args = parser.parse_args()
    if args.limit < 1 or args.max_pages < 1:
        parser.error("--limit et --max-pages doivent être positifs")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        items = collect(
            args.url, args.entreprise, args.domaine, args.limit, args.max_pages,
            args.pages_dir, args.chrome_binary, args.offline_only,
        )
    except (OSError, ValueError, ImportError, WebDriverException) as exc:
        LOG.error("Collecte interrompue : %s", exc)
        return 2
    if not items:
        LOG.error("Aucun avis disponible")
        return 2
    write_exports(items, args.output_dir, args.prefix)
    print(f"{len(items)} avis exportés dans {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
