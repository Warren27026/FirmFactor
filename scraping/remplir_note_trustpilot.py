"""Remplit note_trustpilot et nb_avis_affiche à partir des pages 1 (data/page1_6mois).

Lancer depuis scraping/ :
    python remplir_note_trustpilot.py --test   # affiche, n'écrit rien
    python remplir_note_trustpilot.py          # écrit dans Supabase
"""
import argparse
import csv
import json
import os
from pathlib import Path

import psycopg
from dotenv import find_dotenv, load_dotenv
from parsel import Selector

ICI = Path(__file__).resolve().parent
DOSSIER_PAGE1 = ICI.parent / "data" / "page1_6mois"

SQL = """
update entreprises
set note_trustpilot = %(note)s,
    nb_avis_affiche = coalesce(nb_avis_affiche, %(nb_avis)s)
where domaine = %(domaine)s
"""


def lire_fiche(fichier):
    """(TrustScore, nombre total d'avis) lus dans la page 1."""
    html = fichier.read_text(encoding="utf-8", errors="ignore")
    brut = Selector(text=html).css("script#__NEXT_DATA__::text").get()
    if brut is None:
        return None, None
    bu = json.loads(brut)["props"]["pageProps"].get("businessUnit") or {}
    return bu.get("trustScore"), bu.get("numberOfReviews")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="n'écrit rien dans la base")
    test = parser.parse_args().test

    load_dotenv(find_dotenv(usecwd=True))
    with open(ICI / "entreprises.csv", encoding="utf-8-sig") as f:
        entreprises = list(csv.DictReader(f))

    conn = None if test else psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)
    mises_a_jour = 0
    for e in entreprises:
        fichier = DOSSIER_PAGE1 / f"{e['domaine'].replace('/', '_')}.html"
        if not fichier.exists():
            print(f"⚠️  {e['nom']:22} page 1 absente")
            continue
        note, nb_avis = lire_fiche(fichier)
        print(f"{e['nom']:22} note {note}  ({nb_avis} avis au total)")
        if conn and note is not None:
            mises_a_jour += conn.execute(SQL, {
                "note": note, "nb_avis": nb_avis, "domaine": e["domaine"],
            }).rowcount
    if conn:
        conn.close()
        print(f"\n✅ {mises_a_jour} entreprises mises à jour dans Supabase")


if __name__ == "__main__":
    main()
