"""Importe le corpus figé du 9 novembre 2026 dans Supabase.

Usage :
    python -m scripts.importer_avis_6mois_supabase --dry-run
    python -m scripts.importer_avis_6mois_supabase

La connexion PostgreSQL vient exclusivement de DATABASE_URL (environnement ou .env).
L'import est atomique et une seconde exécution ignore les avis déjà présents.
"""

from __future__ import annotations

import argparse
import csv
import os
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import psycopg
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
COLLECTE = ROOT / "data" / "collectes_6mois_plan" / "2026-11-09"
AVIS = COLLECTE / "avis_6mois.csv"
COUVERTURE = COLLECTE / "couverture_6mois_consolidee.csv"
ENTREPRISES = ROOT / "scraping" / "entreprises.csv"
SECTEURS = {"commerce_en_ligne", "voyages", "livraison_colis"}
STAGE_COLUMNS = (
    "domaine", "id_externe", "note", "titre", "texte", "date_publication",
    "date_experience", "verifie", "reponse_texte", "reponse_date",
    "auteur_nb_avis", "date_collecte",
)


def lire_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def verifier(corpus: Path) -> tuple[list[dict[str, str]], dict[str, dict[str, str]], Counter[str], dict[str, str], dict[str, str]]:
    entreprises = lire_csv(ENTREPRISES)
    couverture = lire_csv(COUVERTURE)
    company = {row["domaine"]: row for row in entreprises}
    status = {row["domaine"]: row for row in couverture}
    if len(company) != len(entreprises) or set(company) != set(status):
        raise ValueError("Entreprises ou couverture incohérentes")
    if any(row["secteur"] not in SECTEURS for row in entreprises):
        raise ValueError("Secteur invalide")
    if any(row["statut"] not in {"complete", "limite_site"} for row in couverture):
        raise ValueError("Statut de couverture non reconnu")

    counts: Counter[str] = Counter()
    oldest: dict[str, str] = {}
    newest_collection: dict[str, str] = {}
    seen: set[tuple[str, str]] = set()
    with corpus.open(encoding="utf-8-sig", newline="") as stream:
        for line, row in enumerate(csv.DictReader(stream), start=2):
            domain = row["domaine"]
            key = (domain, row["id_externe"])
            if domain not in company or not key[1] or key in seen:
                raise ValueError(f"Domaine, identifiant ou doublon invalide, ligne {line}")
            seen.add(key)
            if row["secteur"] != company[domain]["secteur"] or row["entreprise"] != company[domain]["nom"]:
                raise ValueError(f"Métadonnées incohérentes, ligne {line}")
            if row["note"] not in {"1", "2", "3", "4", "5"}:
                raise ValueError(f"Note invalide, ligne {line}")
            published = datetime.fromisoformat(row["date_publication"])
            collected = datetime.fromisoformat(row["date_collecte"])
            if published.tzinfo is None or collected.tzinfo is None:
                raise ValueError(f"Fuseau horaire absent, ligne {line}")
            if not (date.fromisoformat(status[domain]["debut_periode"]) <= published.date() <= date.fromisoformat(status[domain]["fin_periode"])):
                raise ValueError(f"Avis hors de la fenêtre de collecte, ligne {line}")
            if row["date_experience"]:
                date.fromisoformat(row["date_experience"])
            if row["reponse_date"]:
                datetime.fromisoformat(row["reponse_date"])
            if row["verifie"].lower() not in {"", "true", "false"}:
                raise ValueError(f"Vérification invalide, ligne {line}")
            if row["auteur_nb_avis"] and (not row["auteur_nb_avis"].isdigit()):
                raise ValueError(f"Nombre d'avis auteur invalide, ligne {line}")
            counts[domain] += 1
            publication_day = published.date().isoformat()
            oldest[domain] = min(oldest.get(domain, publication_day), publication_day)
            newest_collection[domain] = max(newest_collection.get(domain, row["date_collecte"]), row["date_collecte"])
    if set(counts) != set(company):
        raise ValueError("Une ou plusieurs entreprises n'ont aucun avis")
    for domain, coverage in status.items():
        if counts[domain] != int(coverage["avis_uniques"]):
            raise ValueError(f"Nombre d'avis incohérent pour {domain}")
    return entreprises, status, counts, oldest, newest_collection


def importer(corpus: Path, entreprises: list[dict[str, str]], couverture: dict[str, dict[str, str]],
             counts: Counter[str], oldest: dict[str, str], newest_collection: dict[str, str]) -> tuple[int, int, int]:
    load_dotenv(ROOT / ".env")
    database_url = os.getenv("DATABASE_URL")
    if not database_url or "XXXXXXXX" in database_url:
        raise ValueError("DATABASE_URL absent : renseigner la connexion Supabase dans .env")
    with psycopg.connect(database_url, connect_timeout=20) as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(8150609, 20261109)")
                cur.execute("SELECT current_database(), current_user")
                print("Base cible :", cur.fetchone()[0])
                cur.execute("SELECT to_regclass('public.entreprises'), to_regclass('public.avis')")
                if any(value is None for value in cur.fetchone()):
                    raise ValueError("Les tables public.entreprises et public.avis sont absentes")
                cur.execute("""SELECT domaine, url_trustpilot FROM public.entreprises
                               WHERE domaine = ANY(%s) OR url_trustpilot = ANY(%s)""",
                            ([e["domaine"] for e in entreprises], [e["url_trustpilot"] for e in entreprises]))
                incoming_url = {e["url_trustpilot"]: e["domaine"] for e in entreprises}
                if len(incoming_url) != len(entreprises):
                    raise ValueError("URL Trustpilot dupliquée dans la liste des entreprises")
                for domain, url in cur.fetchall():
                    if incoming_url.get(url, domain) != domain:
                        raise ValueError(f"URL Trustpilot déjà liée à un autre domaine : {url}")

                cur.execute("""CREATE TEMP TABLE import_avis_6mois (
                    domaine text, id_externe text, note text, titre text, texte text,
                    date_publication text, date_experience text, verifie text,
                    reponse_texte text, reponse_date text, auteur_nb_avis text,
                    date_collecte text) ON COMMIT DROP""")
                with cur.copy("COPY import_avis_6mois FROM STDIN") as copy:
                    with corpus.open(encoding="utf-8-sig", newline="") as stream:
                        for row in csv.DictReader(stream):
                            copy.write_row(tuple(row[column] or None for column in STAGE_COLUMNS))
                cur.execute("SELECT count(*) FROM import_avis_6mois")
                if cur.fetchone()[0] != sum(counts.values()):
                    raise ValueError("Le chargement temporaire n'a pas conservé tous les avis")

                for company in entreprises:
                    domain = company["domaine"]
                    complete = couverture[domain]["statut"] == "complete"
                    cur.execute("""INSERT INTO public.entreprises
                        (nom, domaine, secteur, url_trustpilot, nb_avis_affiche,
                         derniere_collecte, avis_6_mois, debut_couverture, couverture_complete)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (domaine) DO UPDATE SET
                            nom = EXCLUDED.nom,
                            secteur = EXCLUDED.secteur,
                            url_trustpilot = EXCLUDED.url_trustpilot,
                            nb_avis_affiche = EXCLUDED.nb_avis_affiche,
                            derniere_collecte = EXCLUDED.derniere_collecte,
                            avis_6_mois = EXCLUDED.avis_6_mois,
                            debut_couverture = EXCLUDED.debut_couverture,
                            couverture_complete = EXCLUDED.couverture_complete""",
                        (company["nom"], domain, company["secteur"], company["url_trustpilot"],
                         int(company["nb_avis_affiche"]) if company["nb_avis_affiche"] else None,
                         newest_collection[domain], counts[domain] if complete else None,
                         oldest[domain], complete))
                cur.execute("""WITH added AS (
                    INSERT INTO public.avis
                    (entreprise_id, id_externe, note, titre, texte, date_publication,
                     date_experience, verifie, reponse_texte, reponse_date,
                     auteur_nb_avis, collecte_le)
                    SELECT e.id, s.id_externe, s.note::smallint, s.titre, s.texte,
                           s.date_publication::timestamptz, s.date_experience::date,
                           s.verifie::boolean, s.reponse_texte, s.reponse_date::timestamptz,
                           s.auteur_nb_avis::integer, s.date_collecte::timestamptz
                    FROM import_avis_6mois s
                    JOIN public.entreprises e ON e.domaine = s.domaine
                    WHERE NOT EXISTS (
                        SELECT 1 FROM public.avis a
                        WHERE a.entreprise_id = e.id AND a.id_externe = s.id_externe
                    )
                    ON CONFLICT DO NOTHING
                    RETURNING id
                ) SELECT count(*) FROM added""")
                inserted = cur.fetchone()[0]
                cur.execute("""SELECT count(*), count(DISTINCT e.domaine)
                               FROM public.avis a JOIN public.entreprises e ON e.id = a.entreprise_id
                               WHERE e.domaine = ANY(%s)""", ([e["domaine"] for e in entreprises],))
                total_reviews, total_companies = cur.fetchone()
                for domain, expected in counts.items():
                    cur.execute("""SELECT count(*) FROM public.avis a
                                   JOIN public.entreprises e ON e.id = a.entreprise_id
                                   WHERE e.domaine = %s AND a.id_externe IN
                                       (SELECT id_externe FROM import_avis_6mois WHERE domaine = %s)""",
                                (domain, domain))
                    if cur.fetchone()[0] != expected:
                        raise ValueError(f"Contrôle après import échoué pour {domain}")
    return inserted, total_reviews, total_companies


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=AVIS)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    entreprises, couverture, counts, oldest, newest_collection = verifier(args.csv)
    print(f"Corpus vérifié : {sum(counts.values()):,} avis, {len(entreprises)} entreprises, "
          f"{sum(row['statut'] == 'complete' for row in couverture.values())} couvertures complètes")
    if args.dry_run:
        return
    inserted, total_reviews, total_companies = importer(
        args.csv, entreprises, couverture, counts, oldest, newest_collection)
    print(f"Import terminé : {inserted:,} nouveaux avis ; {total_reviews:,} avis et "
          f"{total_companies} entreprises désormais présents pour ces domaines")


if __name__ == "__main__":
    main()
