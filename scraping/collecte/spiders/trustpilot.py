"""Collecte des avis Trustpilot des entreprises listées dans scraping/entreprises.csv.

Le spider parcourt les avis du plus récent au plus ancien et s'arrête
dès qu'il atteint des avis plus vieux que la période demandée.

Exemples (depuis le dossier scraping/) :
    scrapy crawl trustpilot -a max_entreprises=1 -a limite_pages=1   # test rapide
    scrapy crawl trustpilot -a secteur=livraison_colis               # 6 derniers mois
    scrapy crawl trustpilot -a domaine=www.colissimo.fr              # une seule entreprise
    scrapy crawl trustpilot -a jours=10                              # collecte hebdomadaire
"""
import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import scrapy

CSV_ENTREPRISES = Path(__file__).resolve().parents[2] / "entreprises.csv"


def lire_date(texte):
    """Convertit '2026-10-19T12:35:01.000Z' en date Python (UTC)."""
    if not texte:
        return None
    return datetime.fromisoformat(texte.replace("Z", "+00:00"))


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

    def __init__(self, secteur=None, domaine=None, mois=6, jours=None,
                 limite_pages=300, max_entreprises=None, **kwargs):
        super().__init__(**kwargs)
        self.secteur = secteur
        self.domaine = domaine
        self.limite_pages = int(limite_pages)
        self.max_entreprises = int(max_entreprises) if max_entreprises else None
        nb_jours = int(jours) if jours else int(mois) * 30
        self.date_min = datetime.now(timezone.utc) - timedelta(days=nb_jours)
        self.bilan = {}  # domaine -> [nb d'avis gardés, date la plus ancienne atteinte]

    async def start(self):
        """Point de départ (Scrapy ≥ 2.13 appelle start(), plus start_requests())."""
        for requete in self.start_requests():
            yield requete

    def start_requests(self):
        self.logger.info("Collecte des avis publiés depuis le %s", self.date_min.date())
        with open(CSV_ENTREPRISES, encoding="utf-8") as f:
            entreprises = [
                e for e in csv.DictReader(f)
                if (not self.secteur or e["secteur"] == self.secteur)
                and (not self.domaine or e["domaine"] == self.domaine)
            ]
        for e in entreprises[: self.max_entreprises]:
            self.bilan[e["domaine"]] = [0, None]
            yield self.requete(e, 1)

    def requete(self, entreprise, page):
        # sort=recency : avis du plus récent au plus ancien
        url = f"{entreprise['url_trustpilot']}?sort=recency&page={page}"
        return scrapy.Request(url, cb_kwargs={"entreprise": entreprise, "page": page})

    def parse(self, response, entreprise, page):
        brut = response.css("script#__NEXT_DATA__::text").get()
        if brut is None:
            self.logger.warning("Pas de données __NEXT_DATA__ sur %s", response.url)
            return
        donnees = json.loads(brut)["props"]["pageProps"]
        domaine = entreprise["domaine"]

        if page == 1:
            fiche = donnees.get("businessUnit") or {}
            yield {
                "type": "entreprise",
                "nom": entreprise["nom"],
                "domaine": domaine,
                "secteur": entreprise["secteur"],
                "url_trustpilot": entreprise["url_trustpilot"],
                "note_trustpilot": fiche.get("trustScore"),
                "nb_avis_affiche": fiche.get("numberOfReviews"),
            }

        trop_ancien = False
        for r in donnees.get("reviews") or []:
            publie = lire_date((r.get("dates") or {}).get("publishedDate"))
            if publie and publie < self.date_min:
                trop_ancien = True  # on a dépassé la période demandée
                continue
            self.bilan[domaine][0] += 1
            self.bilan[domaine][1] = publie
            yield extraire_avis(r, domaine)

        pagination = (donnees.get("filters") or {}).get("pagination") or {}
        derniere_page = min(pagination.get("totalPages") or 1, self.limite_pages)
        if not trop_ancien and page < derniere_page:
            yield self.requete(entreprise, page + 1)

    def closed(self, reason):
        self.logger.info("===== BILAN DE LA COLLECTE =====")
        for domaine, (nb, plus_ancien) in self.bilan.items():
            jusqu_au = plus_ancien.date() if plus_ancien else "-"
            self.logger.info("%-30s %6d avis (jusqu'au %s)", domaine, nb, jusqu_au)
