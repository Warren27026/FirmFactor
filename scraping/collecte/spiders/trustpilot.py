"""Collecte des avis Trustpilot des entreprises listées dans scraping/entreprises.csv.

Exemples (depuis le dossier scraping/) :
    scrapy crawl trustpilot -a max_entreprises=1 -a limite_pages=1
    scrapy crawl trustpilot -a secteur=livraison -a limite_pages=20
"""
import csv
import json
from pathlib import Path

import scrapy

CSV_ENTREPRISES = Path(__file__).resolve().parents[2] / "entreprises.csv"


def extraire_avis(r, domaine):
    """Transforme un avis brut (JSON Trustpilot) en ligne pour la table `avis`.

    Aucune donnée d'identité de l'auteur n'est gardée (ni pseudo, ni identifiant).
    """
    dates = r.get("dates") or {}
    auteur = r.get("consumer") or {}
    reponse = r.get("reply") or {}
    verification = (r.get("labels") or {}).get("verification") or {}
    experience = dates.get("experiencedDate")
    return {
        "type": "avis",
        "domaine": domaine,
        "id_externe": r.get("id"),
        "note": r.get("rating"),
        "titre": r.get("title"),
        "texte": r.get("text"),
        "date_publication": dates.get("publishedDate"),
        "date_experience": experience[:10] if experience else None,
        "verifie": verification.get("isVerified"),
        "reponse_texte": reponse.get("message"),
        "reponse_date": reponse.get("publishedDate"),
        "auteur_nb_avis": auteur.get("numberOfReviews"),
    }


class TrustpilotSpider(scrapy.Spider):
    name = "trustpilot"
    allowed_domains = ("fr.trustpilot.com",)

    def __init__(self, secteur=None, limite_pages=50, max_entreprises=None, **kwargs):
        super().__init__(**kwargs)
        self.secteur = secteur
        self.limite_pages = int(limite_pages)
        self.max_entreprises = int(max_entreprises) if max_entreprises else None

    def start_requests(self):
        with open(CSV_ENTREPRISES, encoding="utf-8") as f:
            entreprises = [
                e for e in csv.DictReader(f)
                if not self.secteur or e["secteur"] == self.secteur
            ]
        for e in entreprises[: self.max_entreprises]:
            yield scrapy.Request(
                e["url_trustpilot"], cb_kwargs={"entreprise": e, "page": 1}
            )

    def parse(self, response, entreprise, page):
        brut = response.css("script#__NEXT_DATA__::text").get()
        if brut is None:
            self.logger.warning("Pas de données __NEXT_DATA__ sur %s", response.url)
            return
        donnees = json.loads(brut)["props"]["pageProps"]

        if page == 1:
            fiche = donnees.get("businessUnit") or {}
            yield {
                "type": "entreprise",
                "nom": entreprise["nom"],
                "domaine": entreprise["domaine"],
                "secteur": entreprise["secteur"],
                "url_trustpilot": entreprise["url_trustpilot"],
                "note_trustpilot": fiche.get("trustScore"),
                "nb_avis_affiche": fiche.get("numberOfReviews"),
            }

        for r in donnees.get("reviews") or []:
            yield extraire_avis(r, entreprise["domaine"])

        pagination = (donnees.get("filters") or {}).get("pagination") or {}
        derniere_page = min(pagination.get("totalPages") or 1, self.limite_pages)
        if page < derniere_page:
            yield scrapy.Request(
                f"{entreprise['url_trustpilot']}?page={page + 1}",
                cb_kwargs={"entreprise": entreprise, "page": page + 1},
            )
