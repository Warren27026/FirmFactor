"""Importe dans Supabase les pages Trustpilot enregistrées (HTML).

Deux rangements sont reconnus :
  1. Un dossier par entreprise (enregistrement manuel) :
         data/pages/www.chronopost.fr/page0001.html
     (un « / » dans le domaine devient « _ », ex. maisonsdumonde.com_fr)
  2. Les fichiers de scripts/download_html.py :
         data/html/001-www_cdiscount_com.html
         data/html/001-www_cdiscount_com-page-2.html

Le nom des fichiers n'a pas d'importance : la note filtrée (stars) et le
nombre d'avis annoncés sont lus dans la page elle-même.

Les données sont lues dans le bloc JSON __NEXT_DATA__ de chaque page :
les 10 données du document de cadrage, sans aucune identité d'auteur.

Lancer depuis le dossier scraping/ :
    python importer_pages.py --test    # vérifie les pages, n'écrit rien
    python importer_pages.py           # écrit dans Supabase
"""
import argparse
import csv
import json
import os
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import unquote

import psycopg
from dotenv import find_dotenv, load_dotenv
from parsel import Selector

from collecte.pipelines import SQL_AVIS, SQL_ENTREPRISE
from collecte.spiders.trustpilot import CSV_ENTREPRISES, extraire_avis

# Couverture de la collecte (colonnes ajoutées par docs/schema.sql)
SQL_ECHANTILLON = """
update entreprises
set avis_6_mois = %(avis_6_mois)s,
    pages_6_mois = %(pages_6_mois)s,
    pages_collectees = %(pages_collectees)s,
    debut_couverture = %(debut_couverture)s,
    couverture_complete = %(couverture_complete)s
where id = %(id)s
"""

DATA = Path(__file__).resolve().parent.parent / "data"
DOSSIER_PAGES = DATA / "pages"   # rangement 1
DOSSIER_HTML = DATA / "html"     # rangement 2


def slug(domaine):
    """Même règle que scripts/download_html.py pour nommer les fichiers."""
    return re.sub(r"[^a-zA-Z0-9_-]+", "_", domaine).strip("_")


def lire_page(fichier):
    """Renvoie les données JSON d'une page enregistrée, ou None si illisible."""
    html = fichier.read_text(encoding="utf-8", errors="ignore")
    brut = Selector(text=html).css("script#__NEXT_DATA__::text").get()
    if brut is None:
        return None
    return json.loads(brut)["props"]["pageProps"]


def fichiers_par_entreprise(entreprises):
    """Associe chaque domaine à la liste de ses fichiers HTML, quel que soit le rangement."""
    par_dossier = {e["domaine"].replace("/", "_"): d for d, e in entreprises.items()}
    par_slug = {slug(d): d for d in entreprises}
    trouves = defaultdict(list)

    if DOSSIER_PAGES.is_dir():
        for dossier in DOSSIER_PAGES.iterdir():
            if not dossier.is_dir():
                continue
            # scripts/scrape_trustpilot_massif.py nomme le dossier avec quote() :
            # maisonsdumonde.com%2Ffr ; ici on accepte aussi maisonsdumonde.com_fr
            # « __s1 » … « __s5 » : recherche par note (scraping/cibles_6mois.csv)
            nom = re.sub(r"__s[1-5]$", "", unquote(dossier.name)).replace("/", "_")
            if nom not in par_dossier:
                print(f"⚠️  {dossier.name} : absent de entreprises.csv, dossier ignoré")
                continue
            trouves[par_dossier[nom]] += dossier.glob("*.htm*")

    if DOSSIER_HTML.is_dir():
        for fichier in DOSSIER_HTML.glob("*.htm*"):
            m = re.fullmatch(r"\d+-(.+?)(?:-page-\d+)?", fichier.stem)
            if not m or m.group(1) not in par_slug:
                print(f"⚠️  {fichier.name} : entreprise non reconnue, fichier ignoré")
                continue
            trouves[par_slug[m.group(1)]].append(fichier)

    return trouves


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--test", action="store_true", help="n'écrit rien dans la base")
    test = parser.parse_args().test

    load_dotenv(find_dotenv(usecwd=True))
    with open(CSV_ENTREPRISES, encoding="utf-8-sig") as f:
        entreprises = {e["domaine"]: e for e in csv.DictReader(f)}

    conn = None if test else psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)
    total_avis, total_nouveaux = 0, 0

    for domaine, fichiers in sorted(fichiers_par_entreprise(entreprises).items()):
        e = entreprises[domaine]
        # nom de l'entreprise tel que Trustpilot l'écrit (ex. colisprivé.fr)
        attendu = unquote(e["url_trustpilot"].split("/review/")[-1])
        fiche, avis, pages_valides, ignorees = {}, {}, 0, 0
        totaux = {}   # nombre d'avis sur 6 mois annoncé par Trustpilot, par recherche (None = toutes notes)
        pages_max = {}
        for fichier in sorted(fichiers):
            page = lire_page(fichier)
            if page is None:
                print(f"⚠️  {fichier.name} : pas de données d'avis (page mal enregistrée ?)")
                continue
            bu = page.get("businessUnit") or {}
            if bu.get("identifyingName") and bu["identifyingName"] != attendu:
                print(f"⚠️  {fichier.name} : page de {bu['identifyingName']}, attendu {attendu}")
                continue
            filtres = page.get("filters") or {}
            if (filtres.get("selected") or {}).get("date") != "last6months":
                ignorees += 1   # page sans le filtre 6 mois : période et totaux faux
                continue
            fiche = bu or fiche
            pages_valides += 1
            etoiles = (filtres.get("selected") or {}).get("stars")
            etoiles = int(etoiles) if etoiles else None
            pagination = filtres.get("pagination") or {}
            if pagination.get("totalCount") is not None:
                totaux[etoiles] = max(totaux.get(etoiles, 0), pagination["totalCount"])
            if pagination.get("totalPages"):
                pages_max[etoiles] = max(pages_max.get(etoiles, 0), pagination["totalPages"])
            for r in page.get("reviews") or []:
                a = extraire_avis(r, domaine)
                avis[a["id_externe"]] = a  # un même avis sur deux pages n'est compté qu'une fois

        if ignorees:
            print(f"⚠️  {domaine} : {ignorees} page(s) sans filtre 6 mois ignorée(s)")
        dates = sorted(a["date_publication"] for a in avis.values() if a["date_publication"])
        periode = f"du {dates[0][:10]} au {dates[-1][:10]}" if dates else "-"

        # Couverture. Recherche simple : complète si on a tous les avis annoncés.
        # Recherche par note : chaque note est complète ou non ; la période où TOUTES
        # les notes sont complètes commence à la date la plus récente parmi les
        # plus anciens avis des notes incomplètes.
        notes_vues = [k for k in totaux if k is not None]
        avis_6_mois = totaux.get(None) or (sum(totaux[k] for k in notes_vues) or None)
        debut_couverture = dates[0][:10] if dates else None
        couverture_complete = bool(avis_6_mois) and len(avis) >= 0.98 * avis_6_mois
        notes_partielles = []
        if notes_vues:
            couverture_complete = True
            for k in notes_vues:
                de_k = sorted(a["date_publication"] for a in avis.values()
                              if a["note"] == k and a["date_publication"])
                if len(de_k) < 0.98 * totaux[k]:
                    couverture_complete = False
                    notes_partielles.append(f"{k}★ {len(de_k)}/{totaux[k]}")
                    if de_k and de_k[0][:10] > (debut_couverture or ""):
                        debut_couverture = de_k[0][:10]
        pages_6_mois = sum(pages_max.values()) or None
        if couverture_complete:
            etat = "6 mois complets"
        else:
            etat = f"complet depuis le {debut_couverture}" + (
                f" (partiel : {', '.join(notes_partielles)})" if notes_partielles else "")
        print(f"{domaine:30} {len(avis):5}/{avis_6_mois} avis  ({periode})  {pages_valides} pages, {etat}")
        total_avis += len(avis)

        if test or not avis:
            continue
        entreprise_id = conn.execute(SQL_ENTREPRISE, {
            "nom": e["nom"], "domaine": domaine, "secteur": e["secteur"],
            "url_trustpilot": e["url_trustpilot"],
            "note_trustpilot": fiche.get("trustScore"),
            "nb_avis_affiche": fiche.get("numberOfReviews"),
        }).fetchone()[0]
        conn.execute(SQL_ECHANTILLON, {
            "id": entreprise_id, "avis_6_mois": avis_6_mois,
            "pages_6_mois": pages_6_mois, "pages_collectees": pages_valides,
            "debut_couverture": debut_couverture, "couverture_complete": couverture_complete,
        })
        for a in avis.values():
            total_nouveaux += conn.execute(SQL_AVIS, {**a, "entreprise_id": entreprise_id}).rowcount

    print(f"\nTotal lu : {total_avis} avis uniques")
    if conn:
        conn.close()
        print(f"✅ {total_nouveaux} nouveaux avis enregistrés dans Supabase (doublons ignorés)")


if __name__ == "__main__":
    main()
