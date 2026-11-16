"""Continue une collecte datée après minuit sans déplacer sa fenêtre d'avis.

Exemple :
    python -m scripts.reprendre_collecte_6mois --snapshot-date 2026-11-09 \
        --csv scraping/cibles_6mois.csv --profile-dir data/chrome_trustpilot_profile

Les avis déjà obtenus restent dans avis_6mois.csv. Le balayage élargi à douze mois
repart de la page 1 lorsqu'on change de jour, car les nouvelles publications
décalent la pagination. Chaque avis est dédupliqué et filtré sur la fenêtre fixe.
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import random
import re
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup

from scripts.scrape_trustpilot_massif import (
    ROOT, fetch_html, load_reviews, page_path, parse_six_month_page,
    read_targets, save_progress, save_reviews, six_month_searches,
    six_month_start, six_month_url, utc_now,
)


LOG = logging.getLogger("reprise_6mois")
PROGRESS_FIELDS = (
    "domaine", "nom", "note_filtre", "debut_periode", "fin_periode", "scan_date",
    "pages_parcourues", "avis_uniques", "statut", "next_page", "erreur", "updated_at",
)
COVERAGE_FIELDS = (
    "domaine", "nom", "debut_periode", "fin_periode", "avis_uniques",
    "recherches_terminees", "recherches_prevues", "statut",
)


def search_key(row: dict[str, str]) -> tuple[str, int | None]:
    return row["domaine"], int(row["note_filtre"]) if row["note_filtre"] else None


def read_progress(path: Path) -> dict[tuple[str, int | None], dict[str, str]]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return {search_key(row): row for row in csv.DictReader(stream)}


def company_domain(domain: str) -> str:
    return re.sub(r"__s[1-5]$", "", domain)


def review_count(frame: pd.DataFrame, domain: str, star: int | None) -> int:
    mask = frame["domaine"] == domain
    if star is not None:
        mask &= frame["note"].astype(str) == str(star)
    return int(mask.sum())


def save_coverage(
    path: Path, searches: list[tuple[dict[str, str], int | None]],
    legacy: dict[tuple[str, int | None], dict[str, str]],
    progress: dict[tuple[str, int | None], dict[str, str]],
    frame: pd.DataFrame, start: date, end: date,
) -> None:
    groups: dict[str, list[tuple[dict[str, str], int | None]]] = {}
    for target, star in searches:
        groups.setdefault(company_domain(target["domaine"]), []).append((target, star))
    rows = []
    for domain, planned in groups.items():
        statuses = [
            (progress.get((target["domaine"], star))
             or legacy.get((target["domaine"], star))
             or {}).get("statut", "a_faire")
            for target, star in planned
        ]
        if all(status == "complete" for status in statuses):
            status = "complete"
        else:
            status = next(
                (value for value in (
                    "error", "limite_site", "max_pages", "incomplet",
                    "partial", "a_faire",
                ) if value in statuses), "partial",
            )
        rows.append({
            "domaine": domain, "nom": planned[0][0]["nom"],
            "debut_periode": start.isoformat(), "fin_periode": end.isoformat(),
            "avis_uniques": review_count(frame, domain, None),
            "recherches_terminees": statuses.count("complete"),
            "recherches_prevues": len(planned), "statut": status,
        })
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".csv.tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=COVERAGE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def collect(
    snapshot: date, csv_path: Path, max_pages: int, profile_dir: Path,
    max_searches: int | None = None,
) -> tuple[int, int]:
    today = datetime.now().date()
    start = six_month_start(snapshot)
    if snapshot > today or six_month_start(six_month_start(today)) > start:
        raise ValueError("La fenêtre fixe est hors de portée du filtre Trustpilot sur douze mois")
    output_dir = ROOT / "data/collectes_6mois_plan" / snapshot.isoformat()
    if not output_dir.is_dir():
        raise ValueError(f"Collecte initiale absente : {output_dir}")
    reviews_path = output_dir / "avis_6mois.csv"
    if not reviews_path.is_file():
        raise ValueError(f"CSV initial absent : {reviews_path}")
    frame = load_reviews(reviews_path)
    legacy = read_progress(output_dir / "suivi_6mois.csv")
    progress_path = output_dir / "suivi_reprise.csv"
    progress = read_progress(progress_path)
    searches = six_month_searches(read_targets(csv_path), True, (1, 2, 3, 4, 5))
    if max_searches is not None:
        searches = searches[:max_searches]
    coverage_path = output_dir / "couverture_6mois_consolidee.csv"
    save_coverage(coverage_path, searches, legacy, progress, frame, start, snapshot)
    failures = 0

    for target, star in searches:
        domain = target["domaine"]
        company = company_domain(domain)
        key = domain, star
        old = legacy.get(key, {})
        state = progress.get(key, {})
        if old.get("statut") in {"complete", "limite_site"} or state.get("statut") in {"complete", "limite_site"}:
            LOG.info("[SKIP] %s note %s : recherche déjà terminée", target["nom"], star)
            continue
        same_scan = state.get("scan_date") == today.isoformat()
        page = int(state.get("next_page") or 1) if same_scan else 1
        pages_done = int(state.get("pages_parcourues") or 0) if same_scan else 0
        count = review_count(frame, company, star)
        downloaded = False

        def checkpoint(status: str, error: str = "") -> None:
            progress[key] = {
                "domaine": domain, "nom": target["nom"], "note_filtre": str(star or ""),
                "debut_periode": start.isoformat(), "fin_periode": snapshot.isoformat(),
                "scan_date": today.isoformat(), "pages_parcourues": str(pages_done),
                "avis_uniques": str(count), "statut": status, "next_page": str(page),
                "erreur": error, "updated_at": utc_now(),
            }
            save_progress(progress, progress_path, PROGRESS_FIELDS)
            save_coverage(coverage_path, searches, legacy, progress, frame, start, snapshot)

        while page <= max_pages:
            if datetime.now().date() != today:
                checkpoint("partial", "Jour changé : relancer pour recalculer la pagination")
                LOG.warning("Jour changé, reprise nécessaire : %s note %s", target["nom"], star)
                return len(frame), failures
            url = six_month_url(target["url_trustpilot"], page, star, "last12months")
            base = six_month_url(target["url_trustpilot"], star=star, date_filter="last12months")
            path = page_path(
                output_dir / "reprise_pages" / today.isoformat() / f"note_{star or 0}",
                domain, page,
            )
            try:
                if path.is_file():
                    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
                    try:
                        reviews, total_pages, total_count, per_page = parse_six_month_page(soup, url, page)
                    except (KeyError, TypeError, ValueError):
                        reviews = None
                else:
                    reviews = None
                if reviews is None:
                    LOG.info("[GET] %s note %s page %s : %s", target["nom"], star, page, url)
                    html = fetch_html(url, base, page, None, profile_dir, require_reviews=False)
                    soup = BeautifulSoup(html, "html.parser")
                    reviews, total_pages, total_count, per_page = parse_six_month_page(soup, url, page)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    temporary = path.with_suffix(".html.tmp")
                    temporary.write_text(html, encoding="utf-8")
                    os.replace(temporary, path)
                    downloaded = True

                known = set(frame.loc[frame["domaine"] == company, "id_externe"])
                additions = []
                reached_start = False
                for review in reviews:
                    published = review["date_publication"]
                    if published is None:
                        raise ValueError("Avis sans date de publication")
                    published_day = published.date()
                    if published_day < start:
                        reached_start = True
                    if not start <= published_day <= snapshot:
                        continue
                    review_id = str(review["id_externe"])
                    if review_id in known:
                        continue
                    additions.append({
                        "entreprise": target["nom"], "domaine": company,
                        "secteur": target["secteur"],
                        **{field: value.isoformat() if isinstance(value, (date, datetime)) else value
                           for field, value in review.items()},
                        "url_source": url, "page": page, "date_collecte": utc_now(),
                    })
                    known.add(review_id)
                if additions:
                    frame = pd.concat([frame, pd.DataFrame(additions)], ignore_index=True)
                    save_reviews(frame, reviews_path)
                count = review_count(frame, company, star)
                pages_done = page
                page += 1
                if reached_start or page > total_pages and total_count <= total_pages * per_page:
                    status = "complete"
                elif page > total_pages and total_count > total_pages * per_page:
                    status = "limite_site"
                elif page > max_pages:
                    status = "max_pages"
                else:
                    status = "partial"
                checkpoint(status)
                LOG.info("[SAVE] %s note %s page %s : %s avis, %s",
                         target["nom"], star, pages_done, count, status)
                if status != "partial":
                    break
                if downloaded:
                    time.sleep(random.uniform(3.0, 5.0))
            except Exception as exc:
                failures += 1
                LOG.exception("[ERROR] %s note %s page %s", target["nom"], star, page)
                checkpoint("error", str(exc)[:300])
                time.sleep(10.0)
                break
        if downloaded:
            time.sleep(random.uniform(10.0, 18.0))
    return len(frame), failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-date", type=date.fromisoformat, required=True)
    parser.add_argument("--csv", type=Path, default=ROOT / "scraping/cibles_6mois.csv")
    parser.add_argument("--max-pages", type=int, default=100)
    parser.add_argument("--max-searches", type=int, help="Limiter les recherches pour une vérification ciblée")
    parser.add_argument("--profile-dir", type=Path, default=ROOT / "data/chrome_trustpilot_profile")
    args = parser.parse_args()
    if args.max_pages < 1 or args.max_searches is not None and args.max_searches < 1:
        parser.error("Les limites doivent être positives")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        total, failures = collect(
            args.snapshot_date, args.csv, args.max_pages, args.profile_dir,
            args.max_searches,
        )
    except (OSError, ValueError) as exc:
        LOG.error("Arrêt : %s", exc)
        return 2
    LOG.info("Terminé : %s avis uniques, %s erreur(s)", total, failures)
    return 2 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
