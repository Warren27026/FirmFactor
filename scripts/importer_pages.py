"""Importe les pages Trustpilot enregistrées vers les tables FirmFactor.

Depuis la racine du projet :
    python -m scripts.importer_pages --dry-run
    python -m scripts.importer_pages

La seconde commande exige DATABASE_URL dans .env. Les pages HTML restent sur disque.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import quote

from dotenv import load_dotenv

from scripts.download_html import ROOT

LOG = logging.getLogger("importer_pages")
NEXT_DATA = re.compile(
    r"<script\b(?=[^>]*\bid\s*=\s*['\"]__NEXT_DATA__['\"])[^>]*>(.*?)</script\s*>",
    re.IGNORECASE | re.DOTALL,
)
SECTEURS = {
    "commerce": "commerce_en_ligne",
    "commerce_en_ligne": "commerce_en_ligne",
    "voyage": "voyages",
    "voyages": "voyages",
    "livraison": "livraison_colis",
    "livraison_colis": "livraison_colis",
}


def _date_utc(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError(f"Date invalide : {value!r}")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError(f"Date sans fuseau : {value!r}")
    return result.astimezone(timezone.utc)


def _entier(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool) or not (
        isinstance(value, int) or isinstance(value, str) and value.isdecimal()
    ):
        raise ValueError(f"Entier invalide : {value!r}")
    result = int(value)
    if result < 0:
        raise ValueError(f"Entier invalide : {value!r}")
    return result


def _texte(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def extraire_avis(avis: dict[str, Any]) -> dict[str, Any]:
    """Mappe un avis JSON sans conserver l'identité de son auteur."""
    identifiant = avis.get("id")
    if not isinstance(identifiant, str) or not identifiant:
        raise ValueError("Avis sans identifiant Trustpilot")
    note = _entier(avis.get("rating"))
    if note is None or not 1 <= note <= 5:
        raise ValueError(f"Note hors de 1 à 5 : {note!r}")
    dates = avis.get("dates") or {}
    labels = avis.get("labels") or {}
    verification = labels.get("verification") or {}
    reply = avis.get("reply") or {}
    consumer = avis.get("consumer") or {}
    verified = verification.get("isVerified")
    if verified is not None and not isinstance(verified, bool):
        raise ValueError(f"Vérification invalide : {verified!r}")
    experience = dates.get("experiencedDate")
    experience_date = date.fromisoformat(experience[:10]) if experience else None
    return {
        "id_externe": identifiant,
        "note": note,
        "titre": _texte(avis.get("title")),
        "texte": _texte(avis.get("text")),
        "date_publication": _date_utc(dates.get("publishedDate")),
        "date_experience": experience_date,
        "verifie": verified,
        "reponse_texte": _texte(reply.get("message")),
        "reponse_date": _date_utc(reply.get("publishedDate")),
        "auteur_nb_avis": _entier(consumer.get("numberOfReviews")),
    }


def extraire_page(html: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    match = NEXT_DATA.search(html)
    if match is None:
        raise ValueError("Bloc __NEXT_DATA__ absent")
    page_props = json.loads(match.group(1))["props"]["pageProps"]
    business = page_props["businessUnit"]
    reviews = page_props["reviews"]
    if not isinstance(business, dict) or not isinstance(reviews, list):
        raise ValueError("Structure __NEXT_DATA__ inattendue")
    try:
        score = Decimal(str(business["trustScore"])) if business.get("trustScore") is not None else None
    except InvalidOperation as exc:
        raise ValueError("trustScore invalide") from exc
    if score is not None and not Decimal("0") <= score <= Decimal("5"):
        raise ValueError("trustScore hors de 0 à 5")
    metrics = {
        "note_trustpilot": score,
        "nb_avis_affiche": _entier(business.get("numberOfReviews")),
    }
    return metrics, [extraire_avis(item) for item in reviews]


def pages_entreprise(pages_dir: Path, domaine: str) -> Iterator[Path]:
    dossier = pages_dir / quote(domaine, safe="")
    if not dossier.is_dir():
        return
    yield from sorted(dossier.glob("page[0-9][0-9][0-9][0-9].html"))


def _enregistrer(conn: Any, cible: dict[str, str], metrics: dict[str, Any], avis: list[dict[str, Any]]) -> int:
    secteur = SECTEURS.get(cible["secteur"])
    if secteur is None:
        raise ValueError(f"Secteur inconnu pour {cible['domaine']} : {cible['secteur']}")
    with conn.cursor() as cursor:
        cursor.execute(
            """INSERT INTO public.entreprises
               (nom, domaine, secteur, url_trustpilot, note_trustpilot, nb_avis_affiche, derniere_collecte)
               VALUES (%s, %s, %s, %s, %s, %s, now())
               ON CONFLICT (domaine) DO UPDATE SET
                 nom = EXCLUDED.nom, secteur = EXCLUDED.secteur,
                 url_trustpilot = EXCLUDED.url_trustpilot,
                 note_trustpilot = EXCLUDED.note_trustpilot,
                 nb_avis_affiche = EXCLUDED.nb_avis_affiche,
                 derniere_collecte = EXCLUDED.derniere_collecte
               RETURNING id""",
            (cible["nom"], cible["domaine"], secteur, cible["url_trustpilot"],
             metrics["note_trustpilot"], metrics["nb_avis_affiche"]),
        )
        entreprise_id = cursor.fetchone()[0]
        inserted = 0
        for item in avis:
            cursor.execute(
                """INSERT INTO public.avis
                   (entreprise_id, id_externe, note, titre, texte, date_publication,
                    date_experience, verifie, reponse_texte, reponse_date, auteur_nb_avis)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (entreprise_id, id_externe) DO NOTHING
                   RETURNING id""",
                (entreprise_id, item["id_externe"], item["note"], item["titre"],
                 item["texte"], item["date_publication"], item["date_experience"],
                 item["verifie"], item["reponse_texte"], item["reponse_date"],
                 item["auteur_nb_avis"]),
            )
            inserted += cursor.fetchone() is not None
    return inserted


def importer(cibles: list[dict[str, str]], pages_dir: Path, conn: Any | None = None) -> tuple[int, int, int, int]:
    pages = unique_count = inserted = errors = 0
    for cible in cibles:
        seen: set[str] = set()
        for path in pages_entreprise(pages_dir, cible["domaine"]):
            try:
                metrics, reviews = extraire_page(path.read_text(encoding="utf-8"))
                unique = []
                for item in reviews:
                    if item["id_externe"] not in seen:
                        unique.append(item)
                        seen.add(item["id_externe"])
                unique_count += len(unique)
                if conn is not None:
                    with conn.transaction():
                        inserted += _enregistrer(conn, cible, metrics, unique)
                pages += 1
                LOG.info("%s : %s avis uniques sur la page", path, len(unique))
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                errors += 1
                LOG.error("Page ignorée %s : %s", path, exc)
    return pages, unique_count, inserted, errors


def main() -> int:
    from scripts.scrape_trustpilot_massif import read_targets

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=ROOT / "scraping/entreprises.csv")
    parser.add_argument("--pages-dir", type=Path, default=ROOT / "data/pages")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--max-entreprises", type=int)
    parser.add_argument("--quiet", action="store_true", help="Afficher seulement les erreurs et le bilan")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    cibles = read_targets(args.csv, args.max_entreprises)
    if args.dry_run:
        pages, unique_count, _, errors = importer(cibles, args.pages_dir)
        print(f"{pages} pages validées, {unique_count} avis uniques, {errors} erreur(s) ; aucune écriture Supabase")
        return 2 if errors else 0
    load_dotenv()
    database_url = os.getenv("DATABASE_URL")
    if not database_url or "XXXXXXXX" in database_url:
        parser.error("DATABASE_URL absent : renseigner .env ou utiliser --dry-run")
    import psycopg

    with psycopg.connect(database_url, connect_timeout=15) as conn:
        pages, unique_count, inserted, errors = importer(cibles, args.pages_dir, conn)
    print(f"{pages} pages traitées, {unique_count} avis uniques, {inserted} avis insérés, {errors} erreur(s)")
    return 2 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
