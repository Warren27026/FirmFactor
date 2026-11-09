"""Enregistre la première page Trustpilot des six derniers mois pour chaque entreprise.

Usage : python -m scripts.download_page1_6mois
Le manifeste scraping/page1_6mois.csv est créé et mis à jour après chaque page.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, unquote, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

from scripts.download_html import create_driver


ROOT = Path(__file__).resolve().parents[1]
FIELDS = ("secteur", "entreprise", "url_a_ouvrir", "nom_du_fichier", "fait")
SECTORS = {"commerce": "commerce_en_ligne", "voyage": "voyages", "livraison": "livraison_colis"}


def target_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or parsed.hostname != "fr.trustpilot.com" or not parsed.path.startswith("/review/"):
        raise ValueError(f"URL Trustpilot invalide : {base_url}")
    query = [(key, value) for key, value in parse_qsl(parsed.query) if key not in {"date", "sort", "page"}]
    query.extend((("date", "last6months"), ("sort", "recency")))
    return urlunparse(parsed._replace(query=urlencode(query)))


def make_rows(source: Path) -> list[dict[str, str]]:
    with source.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not {"nom", "domaine", "secteur", "url_trustpilot"}.issubset(reader.fieldnames):
            raise ValueError("Colonnes requises : nom, domaine, secteur, url_trustpilot")
        companies = list(reader)
    rows = []
    filenames = set()
    for company in companies:
        domain = company["domaine"].strip()
        filename = domain.replace("/", "_") + ".html"
        if not domain or not re.fullmatch(r"[A-Za-z0-9._-]+\.html", filename) or filename in filenames:
            raise ValueError(f"Nom de fichier invalide ou répété : {filename}")
        filenames.add(filename)
        rows.append({
            "secteur": SECTORS[company["secteur"]],
            "entreprise": company["nom"],
            "url_a_ouvrir": target_url(company["url_trustpilot"]),
            "nom_du_fichier": filename,
            "fait": "",
        })
    return rows


def save_manifest(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, delimiter=";")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def load_manifest(path: Path, source: Path) -> list[dict[str, str]]:
    expected = make_rows(source)
    if not path.exists():
        save_manifest(path, expected)
        return expected
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, delimiter=";")
        if reader.fieldnames != list(FIELDS):
            raise ValueError(f"Colonnes incorrectes dans {path}")
        existing = list(reader)
    if len(existing) != len(expected) or any(
        any(old[key] != new[key] for key in FIELDS if key != "fait")
        for old, new in zip(existing, expected)
    ):
        raise ValueError(f"Le manifeste {path} ne correspond plus à la liste d'entreprises")
    return existing


def valid_page(html: str, requested_url: str, actual_url: str | None = None) -> bool:
    if actual_url and not valid_page_url(actual_url, requested_url):
        return False
    soup = BeautifulSoup(html, "html.parser")
    script = soup.select_one("script#__NEXT_DATA__")
    if script is None or not script.string:
        return False
    try:
        data = json.loads(script.string)
    except json.JSONDecodeError:
        return False
    props = data.get("props", {}).get("pageProps", {})
    # La présence des avis structurés prouve que la page n'est pas un écran de connexion.
    reviews = props.get("reviews")
    if not isinstance(reviews, list):
        return False
    # Le filtre doit aussi être attesté par le JSON, même si la barre d'adresse est correcte.
    page_url = props.get("pageUrl")
    return isinstance(page_url, str) and valid_page_url(page_url, requested_url)


def valid_page_url(actual_url: str, requested_url: str) -> bool:
    actual, expected = urlparse(actual_url), urlparse(requested_url)
    query = parse_qs(actual.query)
    return (actual.hostname == expected.hostname and
            unquote(actual.path).rstrip("/") == unquote(expected.path).rstrip("/") and
            query.get("date") == ["last6months"] and query.get("sort") == ["recency"])


def download(rows: list[dict[str, str]], manifest: Path, output: Path, *,
             maximum: int | None = None, chrome_binary: Path | None = None,
             wait_min: float = 4.5, wait_max: float = 6.5) -> tuple[int, int]:
    output.mkdir(parents=True, exist_ok=True)
    saved = failed = 0
    selected = rows[:maximum] if maximum else rows
    for index, row in enumerate(selected, 1):
        destination = output / row["nom_du_fichier"]
        if destination.is_file() and row["fait"] and valid_page(destination.read_text(encoding="utf-8"), row["url_a_ouvrir"]):
            print(f"[SKIP] {row['entreprise']} : {destination}", flush=True)
            continue
        driver = None
        try:
            if destination.is_file() and valid_page(destination.read_text(encoding="utf-8"), row["url_a_ouvrir"]):
                print(f"[EXISTE] {row['entreprise']} : {destination}", flush=True)
            else:
                print(f"[{index}/{len(selected)}] {row['entreprise']} : {row['url_a_ouvrir']}", flush=True)
                driver = create_driver(chrome_binary)
                driver.get(row["url_a_ouvrir"])
                time.sleep(random.uniform(wait_min, wait_max))
                html = driver.page_source
                if not valid_page(html, row["url_a_ouvrir"], driver.current_url):
                    raise ValueError(f"Page d'avis filtrée absente ou redirection vers {driver.current_url}")
                temporary = destination.with_suffix(".html.tmp")
                temporary.write_text(html, encoding="utf-8")
                os.replace(temporary, destination)
                saved += 1
                print(f"[OK] {destination}", flush=True)
            row["fait"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            save_manifest(manifest, rows)
        except Exception as exc:
            failed += 1
            print(f"[ERREUR] {row['entreprise']} : {exc}", flush=True)
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass
        if index < len(selected):
            time.sleep(random.uniform(3.0, 5.0))
    return saved, failed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "scraping/entreprises.csv")
    parser.add_argument("--manifest", type=Path, default=ROOT / "scraping/page1_6mois.csv")
    parser.add_argument("--output", type=Path, default=ROOT / "page1_6mois")
    parser.add_argument("--max-entreprises", type=int)
    parser.add_argument("--chrome-binary", type=Path)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    if args.max_entreprises is not None and args.max_entreprises < 1:
        parser.error("--max-entreprises doit être positif")
    rows = load_manifest(args.manifest, args.source)
    print(f"Manifeste : {args.manifest} ({len(rows)} entreprises)", flush=True)
    if args.prepare_only:
        return 0
    saved, failed = download(rows, args.manifest, args.output,
                             maximum=args.max_entreprises, chrome_binary=args.chrome_binary)
    print(f"Terminé : {saved} page(s) enregistrée(s), {failed} échec(s).", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
