"""Génère la liste des pages Trustpilot à récupérer (6 derniers mois).

Prérequis : la PAGE 1 filtrée sur 6 mois de chaque entreprise, enregistrée dans
    data/page1_6mois/<domaine>.html   (un « / » du domaine devient « _ »)
URL de cette page 1 : <url_trustpilot>?date=last6months&sort=recency

Trustpilot limite chaque recherche à MAX_PAGES pages (100 avec un compte connecté,
accord de l'encadrant ; 10 sans compte).
Règle appliquée :
- avis sur 6 mois ≤ MAX_PAGES × 20 : une recherche « 6 mois » -> 6 mois complets ;
- au-delà : une recherche par note (stars=1 … 5), MAX_PAGES pages max chacune. Le nombre de pages par note est estimé à partir de la
  répartition des notes de l'entreprise (avec une marge).
La couverture réelle est calculée ensuite par importer_pages.py.

Lancer depuis scraping/ :
    python planifier_pages.py
Résultats :
  data/pages_a_recuperer.csv  liste détaillée des pages (contrôle)
  scraping/cibles_6mois.csv   à donner à scripts/scrape_trustpilot_massif.py (--csv) :
                              une ligne par recherche ; « domaine » vaut
                              <domaine>__s<note> pour une recherche par note.
"""
import csv
import json
import math
from pathlib import Path

from parsel import Selector

MAX_PAGES = 100           # avec compte connecté (10 sans compte)
AVIS_PAR_PAGE = 20
MARGE = 1.5               # on prévoit 50 % de pages en plus par note (estimation)
FILTRE = "date=last6months&sort=recency"
NOTES = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}

ICI = Path(__file__).resolve().parent
DATA = ICI.parent / "data"
DOSSIER_PAGE1 = DATA / "page1_6mois"
SORTIE = DATA / "pages_a_recuperer.csv"
CIBLES = ICI / "cibles_6mois.csv"


def lire_page1(fichier):
    """(avis sur 6 mois, pages, filtre date présent ?, répartition des notes)."""
    html = fichier.read_text(encoding="utf-8", errors="ignore")
    brut = Selector(text=html).css("script#__NEXT_DATA__::text").get()
    if brut is None:
        return None
    filtres = json.loads(brut)["props"]["pageProps"].get("filters") or {}
    pag = filtres.get("pagination") or {}
    notes = (filtres.get("reviewStatistics") or {}).get("ratings") or {}
    date_ok = (filtres.get("selected") or {}).get("date") == "last6months"
    return pag.get("totalCount") or 0, pag.get("totalPages") or 0, date_ok, notes


def main():
    with open(ICI / "entreprises.csv", encoding="utf-8-sig") as f:
        entreprises = list(csv.DictReader(f))

    lignes, cibles, simples, par_note = [], [], 0, 0
    for e in entreprises:
        dossier = e["domaine"].replace("/", "_")
        fichier = DOSSIER_PAGE1 / f"{dossier}.html"
        if not fichier.exists():
            print(f"⚠️  {e['nom']:22} page 1 absente : {fichier.name}")
            continue
        infos = lire_page1(fichier)
        if infos is None:
            print(f"⚠️  {e['nom']:22} page 1 illisible")
            continue
        nb_avis, nb_pages, date_ok, notes = infos
        if not date_ok:
            print(f"❌ {e['nom']:22} page 1 SANS le filtre 6 mois -> à réenregistrer")
            continue

        def ajouter(etoiles, page):
            suffixe = f"&stars={etoiles}" if etoiles else ""
            if page == 1:
                cibles.append({
                    "nom": e["nom"], "secteur": e["secteur"],
                    "domaine": e["domaine"] + (f"__s{etoiles}" if etoiles else ""),
                    "url_trustpilot": f"{e['url_trustpilot']}?{FILTRE}{suffixe}",
                })
            lignes.append({
                "secteur": e["secteur"], "entreprise": e["nom"], "domaine": e["domaine"],
                "note": etoiles or "toutes", "page": page,
                "url": f"{e['url_trustpilot']}?{FILTRE}{suffixe}&page={page}",
                "fichier": f"data/pages/{dossier}/s{etoiles or 0}-p{page:02}.html",
            })

        if nb_avis <= MAX_PAGES * AVIS_PAR_PAGE:
            simples += 1
            for p in range(1, min(nb_pages, MAX_PAGES) + 1):
                ajouter(None, p)
            print(f"{e['nom']:22} {nb_avis:6} avis -> {min(nb_pages, MAX_PAGES):2} pages (6 mois complets)")
            continue

        par_note += 1
        total_notes = sum(notes.get(n, 0) for n in NOTES.values()) or 1
        detail = []
        for etoiles, cle in NOTES.items():
            estime = nb_avis * notes.get(cle, 0) / total_notes
            pages = min(MAX_PAGES, max(1, math.ceil(estime * MARGE / AVIS_PAR_PAGE)))
            detail.append(f"{etoiles}★:{pages}")
            for p in range(1, pages + 1):
                ajouter(etoiles, p)
        print(f"{e['nom']:22} {nb_avis:6} avis -> par note  {' '.join(detail)}")

    DATA.mkdir(exist_ok=True)
    with open(SORTIE, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["secteur", "entreprise", "domaine", "note",
                                          "page", "url", "fichier"])
        w.writeheader()
        w.writerows(lignes)
    with open(CIBLES, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["nom", "secteur", "domaine", "url_trustpilot"])
        w.writeheader()
        w.writerows(cibles)
    print(f"✅ {len(cibles)} recherches -> {CIBLES}")
    print(f"\n{simples} entreprises en recherche simple, {par_note} découpées par note")
    print(f"✅ {len(lignes)} pages à récupérer -> {SORTIE}")


if __name__ == "__main__":
    main()
