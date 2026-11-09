"""Collecte des avis Trustpilot avec Chrome, BeautifulSoup et reprise sur disque.

Depuis la racine du projet :
    python -m scripts.scrape_trustpilot_massif --quota 500 --max-pages 30
    python -m scripts.scrape_trustpilot_massif --six-months --profile-dir data/chrome_trustpilot_profile

Les HTML sont conservés dans data/pages/<domaine>/, ignoré par Git. Relancer la même
commande reprend chaque entreprise à sa prochaine page. Le mode --offline-only
analyse uniquement les HTML déjà enregistrés, sans ouvrir Chrome.

Le mode --six-months utilise date=last6months&sort=recency et les cinq notes ; il conserve séparément
CSV, pages et suivi dans data/collectes_6mois/AAAA-MM-JJ/. Relancer le même jour
avec la même --snapshot-date pour reprendre : le filtre du site est glissant.
Le statut complete exige la dernière page et un
nombre d'avis uniques au moins égal au total annoncé ; limite_site indique que
Trustpilot annonce plus d'avis que ne peuvent contenir les pages accessibles.
"""

from __future__ import annotations

import argparse
import calendar
import csv
import hashlib
import json
import logging
import os
import random
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, unquote, urlencode, urljoin, urlparse, urlunparse

import pandas as pd
from bs4 import BeautifulSoup, Tag
from selenium.common.exceptions import WebDriverException

from scripts.download_html import ROOT, create_driver, html_path_for, is_trustpilot_url, page_path_for
from scripts.importer_pages import extraire_avis


LOG = logging.getLogger("trustpilot_massif")
REVIEW_FIELDS = (
    "entreprise", "domaine", "secteur", "id_externe",
    "date_publication", "note", "titre", "texte", "url_source", "page",
    "date_collecte",
    "date_experience", "verifie", "reponse_texte", "reponse_date",
    "auteur_nb_avis",
)
SUPPLEMENT_FIELDS = (
    "date_experience", "verifie", "reponse_texte", "reponse_date",
    "auteur_nb_avis",
)
PROGRESS_FIELDS = (
    "domaine", "nom", "next_page", "next_url", "avis_uniques", "statut",
    "updated_at", "erreur",
)
SIX_PROGRESS_FIELDS = (
    "domaine", "nom", "note_filtre", "debut_periode", "date_collecte", "pages_parcourues",
    "avis_uniques", "avis_annonces", "statut", "next_page", "erreur", "updated_at",
)
SIX_COVERAGE_FIELDS = (
    "domaine", "nom", "debut_periode", "date_collecte", "avis_uniques",
    "avis_annonces", "pages_parcourues", "notes_terminees", "statut",
)
BLOCK_TITLES = ("access denied", "forbidden", "captcha", "verifying connection")
SECTEURS = {
    "commerce": "commerce_en_ligne",
    "commerce_en_ligne": "commerce_en_ligne",
    "voyage": "voyages",
    "voyages": "voyages",
    "livraison": "livraison_colis",
    "livraison_colis": "livraison_colis",
}


@dataclass(frozen=True)
class Settings:
    csv_path: Path
    html_dir: Path
    output_dir: Path
    quota: int = 500
    max_pages: int = 30
    max_companies: int | None = None
    chrome_binary: Path | None = None
    profile_dir: Path | None = None
    offline_only: bool = False
    six_months: bool = False
    snapshot_date: date | None = None
    star_ratings: tuple[int, ...] = (1, 2, 3, 4, 5)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean_text(node: Tag | None) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip() if node else ""


def read_targets(path: Path, maximum: int | None = None) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames or not {"nom", "url_trustpilot"}.issubset(reader.fieldnames):
            raise ValueError("Le CSV doit contenir nom et url_trustpilot")
        rows = list(reader)
    if maximum is not None:
        rows = rows[:maximum]
    targets = []
    for line, row in enumerate(rows, start=2):
        url = (row.get("url_trustpilot") or "").strip()
        parsed = urlparse(url)
        if not is_trustpilot_url(url) or not parsed.path.startswith("/review/"):
            raise ValueError(f"Ligne {line} : URL Trustpilot invalide : {url}")
        domain = (row.get("domaine") or "").strip() or unquote(
            parsed.path.split("/")[2]
        )
        secteur_source = (row.get("secteur") or "").strip()
        if secteur_source and secteur_source not in SECTEURS:
            raise ValueError(f"Ligne {line} : secteur inconnu : {secteur_source}")
        targets.append({
            "nom": (row.get("nom") or "").strip(),
            "domaine": domain,
            "secteur": SECTEURS.get(secteur_source, ""),
            "url_trustpilot": url,
        })
        if not targets[-1]["nom"] or not domain:
            raise ValueError(f"Ligne {line} : nom ou domaine manquant")
    return targets


def _review_id(article: Tag, title: str, body: str, published: str) -> str:
    review_link = article.select_one('a[href*="/reviews/"]')
    if review_link:
        review_id = urlparse(str(review_link.get("href", ""))).path.rstrip("/").split("/")[-1]
        if review_id:
            return review_id
    review_id = article.get("data-review-id")
    if review_id:
        return str(review_id)

    # /users/ identifie l'auteur, pas l'avis : on lui associe date et contenu.
    profile_link = article.select_one('a[href*="/users/"]')
    profile_id = (
        urlparse(str(profile_link.get("href", ""))).path.rstrip("/").split("/")[-1]
        if profile_link else ""
    )
    fingerprint = "|".join((profile_id, published, title, body))
    return "fallback-" + hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:24]


def _rating(article: Tag) -> int | None:
    rating_node = article.select_one("[data-rating], [data-service-review-rating]")
    if rating_node:
        raw = rating_node.get("data-rating") or rating_node.get("data-service-review-rating")
        if str(raw) in {"1", "2", "3", "4", "5"}:
            return int(str(raw))
    for image in article.select("img[alt]"):
        alt = str(image.get("alt", ""))
        if "étoile" in alt.lower() or "star" in alt.lower():
            match = re.search(r"\b([1-5])\b", alt)
            if match:
                return int(match.group(1))
    return None


def _body(article: Tag) -> str:
    primary = article.select_one("p[data-service-review-text-typography]")
    if primary:
        return clean_text(primary)
    candidates = []
    for paragraph in article.select("p"):
        if paragraph.find_parent(attrs={"data-service-review-business-reply": True}):
            continue
        classes = " ".join(paragraph.get("class", []))
        if "Typography" in classes or "typography" in classes:
            value = clean_text(paragraph)
            if len(value) >= 20:
                candidates.append(value)
    if not candidates:
        candidates = [
            clean_text(p) for p in article.select("p")
            if not p.find_parent(attrs={"data-service-review-business-reply": True})
            and len(clean_text(p)) >= 20
        ]
    return max(candidates, key=len, default="")


def parse_articles(soup: BeautifulSoup) -> list[dict[str, str | int | None]]:
    """Extrait les cartes d'avis principales, sans les cartes de carrousel."""
    structured = {}
    script = soup.select_one("script#__NEXT_DATA__")
    if script is not None and script.string:
        try:
            raw_reviews = json.loads(script.string)["props"]["pageProps"]["reviews"]
            structured = {
                item["id"]: extraire_avis(item)
                for item in raw_reviews
                if isinstance(item, dict) and isinstance(item.get("id"), str)
            }
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            LOG.warning("JSON d'avis inexploitable ; champs complémentaires laissés vides")
    reviews = []
    for article in soup.select("article"):
        classes = " ".join(article.get("class", []))
        if "carouselReviewCard" in classes:
            continue
        if not (
            article.select_one('[data-testid="service-review-card-v2"]')
            or article.select_one('a[href*="/reviews/"]')
            or article.get("data-review-id")
        ):
            continue
        title = clean_text(article.select_one("h2"))
        body = _body(article)
        if not title and not body:
            continue
        published_node = article.select_one("time[data-service-review-date-time-ago][datetime]")
        published_node = published_node or article.select_one("time[datetime]")
        published = str(published_node.get("datetime", "")) if published_node else ""
        review_id = _review_id(article, title, body, published)
        supplement = structured.get(review_id, {})
        reviews.append({
            "id_externe": review_id,
            "date_publication": published,
            "note": _rating(article),
            "titre": title,
            "texte": body,
            **{field: supplement.get(field) for field in SUPPLEMENT_FIELDS},
        })
    return reviews


def next_page_url(soup: BeautifulSoup, current_url: str, base_url: str, page: int) -> str | None:
    link = soup.select_one('a[rel="next"][href]')
    if link:
        url = urljoin(current_url, str(link["href"]))
    else:
        script = soup.select_one("script#__NEXT_DATA__")
        if script is None or not script.string:
            return None
        try:
            props = json.loads(script.string)["props"]["pageProps"]
            total_pages = int(props["filters"]["pagination"]["totalPages"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        if page > total_pages:
            return None
        parsed_current = urlparse(current_url)
        query = dict(parse_qsl(parsed_current.query, keep_blank_values=True))
        query["page"] = str(page)
        url = urlunparse(parsed_current._replace(query=urlencode(query), fragment=""))
    parsed, base = urlparse(url), urlparse(base_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != base.hostname
        or parsed.path.rstrip("/") != base.path.rstrip("/")
        or parse_qs(parsed.query).get("page") != [str(page)]
    ):
        raise ValueError(f"Lien de pagination inattendu : {url}")
    return url


def page_url(base_url: str, page: int) -> str:
    if page == 1:
        return base_url
    parsed = urlparse(base_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["page"] = str(page)
    return urlunparse(parsed._replace(query=urlencode(query), fragment=""))


def load_reviews(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=REVIEW_FIELDS)
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    if not {"domaine", "id_externe"}.issubset(frame.columns):
        raise ValueError(f"Colonnes domaine/id_externe absentes de {path}")
    frame = frame.drop(columns=["auteur"], errors="ignore").drop_duplicates(
        subset=["domaine", "id_externe"]
    )
    for field in SUPPLEMENT_FIELDS:
        if field not in frame.columns:
            frame[field] = ""
    frame["secteur"] = frame["secteur"].replace(SECTEURS)
    return frame.reindex(columns=REVIEW_FIELDS)


def load_progress(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as file:
        return {row["domaine"]: row for row in csv.DictReader(file)}


def save_reviews(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False, encoding="utf-8-sig")
    os.replace(temporary, path)


def save_progress(
    progress: dict[str, dict[str, str]], path: Path,
    fields: tuple[str, ...] = PROGRESS_FIELDS,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(progress.values())
    os.replace(temporary, path)


def fetch_html(
    url: str, base_url: str, page: int, chrome_binary: Path | None,
    profile_dir: Path | None = None, require_reviews: bool = True,
) -> str:
    driver = create_driver(chrome_binary, profile_dir)
    try:
        driver.get(url)
        time.sleep(random.uniform(4.5, 6.5))
        final = urlparse(driver.current_url)
        base = urlparse(base_url)
        if final.hostname != base.hostname or final.path.rstrip("/") != base.path.rstrip("/"):
            raise ValueError(f"Redirection inattendue : {driver.current_url}")
        if page > 1 and parse_qs(final.query).get("page") != [str(page)]:
            raise ValueError(f"La page {page} a redirigé vers {driver.current_url}")
        for key in ("date", "sort"):
            expected = parse_qs(base.query).get(key)
            if expected and parse_qs(final.query).get(key) != expected:
                raise ValueError(f"Filtre {key} perdu : {driver.current_url}")
        html = driver.page_source
        if any(marker in driver.title.lower() for marker in BLOCK_TITLES):
            raise ValueError(f"Page de vérification ou de blocage : {driver.title}")
        if require_reviews and not parse_articles(BeautifulSoup(html, "html.parser")):
            raise ValueError("Aucun avis sur la page chargée")
        return html
    finally:
        try:
            driver.quit()
        except WebDriverException:
            LOG.warning("Chrome n'a pas répondu pendant sa fermeture")


def _count(frame: pd.DataFrame, domain: str) -> int:
    return int((frame["domaine"] == domain).sum())


def page_path(pages_dir: Path, domain: str, page: int) -> Path:
    """Emplacement brut stable, indépendant de l'ordre du CSV des cibles."""
    return page_path_for({"domaine": domain}, pages_dir, page)


def _state(
    target: dict[str, str], page: int, url: str, count: int, status: str, error: str = ""
) -> dict[str, str]:
    return {
        "domaine": target["domaine"],
        "nom": target["nom"],
        "next_page": str(page),
        "next_url": url,
        "avis_uniques": str(count),
        "statut": status,
        "updated_at": utc_now(),
        "erreur": error,
    }


def six_month_start(day: date) -> date:
    """Six mois calendaires avant la date de collecte."""
    month_index = day.year * 12 + day.month - 1 - 6
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def six_month_url(base_url: str, page: int = 1, star: int | None = None) -> str:
    parsed = urlparse(base_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.pop("page", None)
    query.update(date="last6months", sort="recency")
    if star is not None:
        if star not in (1, 2, 3, 4, 5):
            raise ValueError(f"Note de filtre invalide : {star}")
        query["stars"] = str(star)
    else:
        query.pop("stars", None)
    if page > 1:
        query["page"] = str(page)
    return urlunparse(parsed._replace(query=urlencode(query), fragment=""))


def parse_six_month_page(
    soup: BeautifulSoup, expected_url: str, page: int,
) -> tuple[list[dict[str, object]], int, int, int]:
    """Valide le filtre et lit tous les avis structurés, même sans carte HTML."""
    script = soup.select_one("script#__NEXT_DATA__")
    if script is None or not script.string:
        raise ValueError("Bloc __NEXT_DATA__ absent")
    props = json.loads(script.string)["props"]["pageProps"]
    actual = urlparse(props["pageUrl"])
    expected = urlparse(expected_url)
    actual_query = parse_qs(actual.query)
    expected_query = parse_qs(expected.query)
    if (actual.hostname != expected.hostname
            or unquote(actual.path).rstrip("/") != unquote(expected.path).rstrip("/")
            or actual_query.get("date") != ["last6months"]
            or actual_query.get("sort") != ["recency"]
            or actual_query.get("stars") != expected_query.get("stars")
            or (int(actual_query.get("page", ["1"])[0]) != page)):
        raise ValueError(f"Page ou filtre inattendu : {props['pageUrl']}")
    pagination = props["filters"]["pagination"]
    total_pages = int(pagination["totalPages"])
    total_count = int(pagination["totalCount"])
    per_page = int(pagination["perPage"])
    if (int(pagination["currentPage"]) != page or total_pages < 0
            or total_count < 0 or per_page < 1):
        raise ValueError("Pagination incohérente")
    raw = props["reviews"]
    if not isinstance(raw, list):
        raise ValueError("Liste d'avis absente")
    reviews = [extraire_avis(item) for item in raw]
    if expected_query.get("stars") and any(
        review["note"] != int(expected_query["stars"][0]) for review in reviews
    ):
        raise ValueError("Une page contient des avis d'une autre note")
    return reviews, total_pages, total_count, per_page


def save_six_coverage(
    progress: dict[tuple[str, int], dict[str, str]],
    targets: list[dict[str, str]], stars: tuple[int, ...],
    start: date, day: date, path: Path,
) -> None:
    """Résume la couverture des cinq notes pour chaque entreprise."""
    rows = []
    for target in targets:
        parts = [progress.get((target["domaine"], star), {}) for star in stars]
        statuses = {part.get("statut", "a_faire") for part in parts}
        if statuses == {"complete"}:
            status = "complete"
        else:
            status = next(
                (value for value in (
                    "error", "limite_site", "incomplet", "max_pages",
                    "needs_download", "partial", "a_faire",
                ) if value in statuses), "incomplet",
            )
        rows.append({
            "domaine": target["domaine"], "nom": target["nom"],
            "debut_periode": start.isoformat(), "date_collecte": day.isoformat(),
            "avis_uniques": str(sum(int(part.get("avis_uniques") or 0) for part in parts)),
            "avis_annonces": str(sum(int(part.get("avis_annonces") or 0) for part in parts)),
            "pages_parcourues": str(sum(int(part.get("pages_parcourues") or 0) for part in parts)),
            "notes_terminees": str(sum(part.get("statut") == "complete" for part in parts)),
            "statut": status,
        })
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".csv.tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=SIX_COVERAGE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def collect_six_months(settings: Settings) -> tuple[int, int]:
    """Collecte une fenêtre datée, isolée des fichiers de la collecte massive."""
    day = settings.snapshot_date or datetime.now().date()
    if not settings.offline_only and day != datetime.now().date():
        raise ValueError("Le filtre last6months est glissant : recommencez avec la date du jour")
    start = six_month_start(day)
    reviews_path = settings.output_dir / "avis_6mois.csv"
    progress_path = settings.output_dir / "suivi_6mois.csv"
    frame = load_reviews(reviews_path)
    if progress_path.exists():
        with progress_path.open(encoding="utf-8-sig", newline="") as stream:
            progress = {
                (row["domaine"], int(row["note_filtre"])): row
                for row in csv.DictReader(stream)
            }
    else:
        progress = {}
    failures = 0
    targets = read_targets(settings.csv_path, settings.max_companies)

    for target in targets:
        domain = target["domaine"]
        for star in settings.star_ratings:
            key = (domain, star)
            state = progress.get(key, {})
            count = int(((frame["domaine"] == domain) & (frame["note"].astype(str) == str(star))).sum())
            if state and (state.get("debut_periode") != start.isoformat()
                          or state.get("date_collecte") != day.isoformat()):
                raise ValueError(f"Fenêtre incompatible dans {progress_path} pour {domain}")
            if state and count < int(state.get("avis_uniques") or 0):
                raise ValueError(f"CSV incomplet par rapport au suivi pour {domain}, note {star}")
            if state.get("statut") in {"complete", "limite_site", "incomplet"}:
                LOG.info("[SKIP] %s note %s : %s", target["nom"], star, state["statut"])
                continue
            page = int(state.get("next_page") or 1)
            pages_done = int(state.get("pages_parcourues") or 0)
            announced = int(state.get("avis_annonces") or 0)
            downloaded = False

            def checkpoint(status: str, error: str = "") -> None:
                progress[key] = {
                    "domaine": domain, "nom": target["nom"], "note_filtre": str(star),
                    "debut_periode": start.isoformat(), "date_collecte": day.isoformat(),
                    "pages_parcourues": str(pages_done), "avis_uniques": str(count),
                    "avis_annonces": str(announced), "statut": status,
                    "next_page": str(page), "erreur": error,
                    "updated_at": utc_now(),
                }
                save_progress(progress, progress_path, SIX_PROGRESS_FIELDS)
                save_six_coverage(
                    progress, targets, settings.star_ratings, start, day,
                    settings.output_dir / "couverture_6mois.csv",
                )

            while page <= settings.max_pages:
                url = six_month_url(target["url_trustpilot"], page, star)
                path = page_path(settings.html_dir / f"note_{star}", domain, page)
                try:
                    if not settings.offline_only and datetime.now().date() != day:
                        raise ValueError("La date a changé pendant la collecte ; nouvelle collecte datée nécessaire")
                    if path.is_file():
                        html = path.read_text(encoding="utf-8")
                        soup = BeautifulSoup(html, "html.parser")
                        try:
                            reviews, total_pages, announced, per_page = parse_six_month_page(soup, url, page)
                        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                            if settings.offline_only:
                                raise
                            LOG.warning("Page en cache invalide : %s ; nouveau téléchargement", path)
                            reviews = None
                    else:
                        reviews = None
                    if reviews is None:
                        if settings.offline_only:
                            checkpoint("needs_download")
                            break
                        LOG.info("[GET 6 MOIS] %s note %s page %s : %s", target["nom"], star, page, url)
                        html = fetch_html(
                            url, target["url_trustpilot"], page,
                            settings.chrome_binary, settings.profile_dir,
                            require_reviews=False,
                        )
                        soup = BeautifulSoup(html, "html.parser")
                        reviews, total_pages, announced, per_page = parse_six_month_page(soup, url, page)
                        path.parent.mkdir(parents=True, exist_ok=True)
                        temporary = path.with_suffix(".html.tmp")
                        temporary.write_text(html, encoding="utf-8")
                        os.replace(temporary, path)
                        downloaded = True

                    known = set(frame.loc[frame["domaine"] == domain, "id_externe"])
                    additions = []
                    for review in reviews:
                        published = review["date_publication"]
                        if published is None:
                            raise ValueError("Avis sans date de publication")
                        published_day = published.date()
                        if not start <= published_day <= day:
                            continue
                        review_id = str(review["id_externe"])
                        if review_id in known:
                            continue
                        additions.append({
                            "entreprise": target["nom"], "domaine": domain,
                            "secteur": target["secteur"],
                            **{field: value.isoformat() if isinstance(value, (date, datetime)) else value
                               for field, value in review.items()},
                            "url_source": url, "page": page, "date_collecte": utc_now(),
                        })
                        known.add(review_id)
                    if additions:
                        frame = pd.concat([frame, pd.DataFrame(additions)], ignore_index=True)
                        save_reviews(frame, reviews_path)
                    count = int(((frame["domaine"] == domain) & (frame["note"].astype(str) == str(star))).sum())
                    pages_done = page
                    page += 1
                    if page > max(total_pages, 1):
                        status = "complete" if count >= announced else "incomplet"
                        if announced > total_pages * per_page:
                            status = "limite_site"
                        checkpoint(status)
                        LOG.info("[FIN 6 MOIS] %s note %s : %s/%s avis, %s",
                                 target["nom"], star, count, announced, status)
                        break
                    if page > settings.max_pages:
                        checkpoint("max_pages")
                        break
                    checkpoint("partial")
                    if downloaded:
                        time.sleep(random.uniform(3.0, 5.0))
                except Exception as exc:
                    failures += 1
                    LOG.exception("[ERROR 6 MOIS] %s note %s page %s", target["nom"], star, page)
                    checkpoint("error", str(exc)[:300])
                    if not settings.offline_only:
                        time.sleep(10.0)
                    break
            if downloaded:
                time.sleep(random.uniform(10.0, 18.0))
    return len(frame), failures


def collect(settings: Settings) -> tuple[int, int]:
    targets = read_targets(settings.csv_path, settings.max_companies)
    reviews_path = settings.output_dir / "avis_massifs.csv"
    progress_path = settings.output_dir / "scraping_progress_massif.csv"
    frame = load_reviews(reviews_path)
    progress = load_progress(progress_path)
    failures = 0

    for company_index, target in enumerate(targets, start=1):
        domain = target["domaine"]
        count = _count(frame, domain)
        state = progress.get(domain, {})
        if count >= settings.quota:
            LOG.info("[SKIP] %s : %s avis", target["nom"], count)
            continue

        page = int(state.get("next_page") or 1)
        url = state.get("next_url") or page_url(target["url_trustpilot"], page)
        if state.get("statut") == "complete":
            previous_path = page_path(settings.html_dir, domain, page - 1)
            if not previous_path.is_file():
                LOG.info("[SKIP] %s : dernière page introuvable", target["nom"])
                continue
            previous_soup = BeautifulSoup(previous_path.read_text(encoding="utf-8"), "html.parser")
            continuation = next_page_url(
                previous_soup, page_url(target["url_trustpilot"], page - 1),
                target["url_trustpilot"], page,
            )
            if continuation is None:
                LOG.info("[SKIP] %s : dernière page atteinte", target["nom"])
                continue
            url = continuation
        if page > settings.max_pages:
            LOG.info("[SKIP] %s : limite de %s pages atteinte", target["nom"], settings.max_pages)
            continue
        downloaded = False

        while page <= settings.max_pages and count < settings.quota:
            path = page_path(settings.html_dir, domain, page)
            legacy_path = html_path_for(target, company_index, ROOT / "data/html", page)
            if not path.exists() and legacy_path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                os.replace(legacy_path, path)
            cached = path.exists()
            try:
                if cached:
                    html = path.read_text(encoding="utf-8")
                    soup = BeautifulSoup(html, "html.parser")
                    if not parse_articles(soup):
                        cached = False
                if not cached:
                    if settings.offline_only:
                        progress[domain] = _state(target, page, url, count, "needs_download")
                        save_progress(progress, progress_path)
                        break
                    LOG.info("[GET] %s page %s : %s", target["nom"], page, url)
                    html = fetch_html(
                        url, target["url_trustpilot"], page,
                        settings.chrome_binary, settings.profile_dir,
                    )
                    soup = BeautifulSoup(html, "html.parser")
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(html, encoding="utf-8")
                    downloaded = True

                parsed = parse_articles(soup)
                if not parsed:
                    raise ValueError("Aucun avis extrait")
                additions = []
                known_ids = set(frame.loc[frame["domaine"] == domain, "id_externe"])
                for review in parsed:
                    review_id = str(review["id_externe"])
                    if review_id in known_ids:
                        continue
                    additions.append({
                        "entreprise": target["nom"],
                        "domaine": domain,
                        "secteur": target["secteur"],
                        **review,
                        "url_source": url,
                        "page": page,
                        "date_collecte": utc_now(),
                    })
                    known_ids.add(review_id)
                    if count + len(additions) >= settings.quota:
                        break
                if not additions and not cached:
                    raise ValueError("La page téléchargée ne contient aucun nouvel avis")
                if additions:
                    frame = pd.concat([frame, pd.DataFrame(additions)], ignore_index=True)
                    frame = frame.drop_duplicates(subset=["domaine", "id_externe"], keep="first")
                count = _count(frame, domain)
                save_reviews(frame, reviews_path)

                following = next_page_url(soup, url, target["url_trustpilot"], page + 1)
                if count >= settings.quota:
                    status = "quota"
                elif following is None:
                    status = "complete"
                elif page >= settings.max_pages:
                    status = "max_pages"
                else:
                    status = "partial"
                progress[domain] = _state(
                    target, page + 1, following or "", count, status
                )
                save_progress(progress, progress_path)
                LOG.info("[SAVE] %s page %s : %s avis uniques, état %s", target["nom"], page, count, status)
                if status != "partial":
                    break
                page += 1
                url = following
                if not cached:
                    time.sleep(random.uniform(3.0, 5.0))
            except Exception as exc:
                failures += 1
                LOG.exception("[ERROR] %s page %s", target["nom"], page)
                progress[domain] = _state(target, page, url, count, "error", str(exc)[:300])
                save_progress(progress, progress_path)
                if not settings.offline_only:
                    time.sleep(10.0)
                break

        if downloaded and company_index < len(targets):
            time.sleep(random.uniform(10.0, 18.0))

    return len(frame), failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=ROOT / "scraping/entreprises.csv")
    parser.add_argument("--html-dir", type=Path, default=ROOT / "data/pages")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/massif")
    parser.add_argument("--quota", type=int, default=500)
    parser.add_argument("--max-pages", type=int,
                        help="Plafond de sécurité (30 en mode massif, 1000 en mode six mois)")
    parser.add_argument("--max-entreprises", type=int)
    parser.add_argument("--chrome-binary", type=Path)
    parser.add_argument(
        "--profile-dir", type=Path,
        help="Profil Chrome dédié déjà connecté à Trustpilot (fermer sa fenêtre avant la collecte)",
    )
    parser.add_argument("--offline-only", action="store_true")
    parser.add_argument(
        "--six-months", action="store_true",
        help="Collecter toutes les pages filtrées des six derniers mois dans un dossier daté séparé",
    )
    parser.add_argument(
        "--snapshot-date", type=date.fromisoformat,
        help="Date AAAA-MM-JJ de la collecte six mois (à réutiliser pour une reprise ultérieure)",
    )
    args = parser.parse_args()
    max_pages = args.max_pages if args.max_pages is not None else (1000 if args.six_months else 30)
    if args.quota < 1 or max_pages < 1:
        parser.error("--quota et --max-pages doivent être positifs")
    if args.max_entreprises is not None and args.max_entreprises < 1:
        parser.error("--max-entreprises doit être positif")

    if args.snapshot_date and not args.six_months:
        parser.error("--snapshot-date nécessite --six-months")
    snapshot_date = args.snapshot_date or datetime.now().date()
    output_dir = args.output_dir
    html_dir = args.html_dir
    if args.six_months:
        if output_dir == ROOT / "data/massif":
            output_dir = ROOT / "data/collectes_6mois" / snapshot_date.isoformat()
        if html_dir == ROOT / "data/pages":
            html_dir = output_dir / "pages"

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings(
        csv_path=args.csv,
        html_dir=html_dir,
        output_dir=output_dir,
        quota=args.quota,
        max_pages=max_pages,
        max_companies=args.max_entreprises,
        chrome_binary=args.chrome_binary,
        profile_dir=args.profile_dir,
        offline_only=args.offline_only,
        six_months=args.six_months,
        snapshot_date=snapshot_date if args.six_months else None,
    )
    try:
        total, failures = collect_six_months(settings) if args.six_months else collect(settings)
    except (OSError, ValueError) as exc:
        LOG.error("Arrêt : %s", exc)
        return 2
    LOG.info("Terminé : %s avis uniques, %s erreur(s)", total, failures)
    return 2 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
