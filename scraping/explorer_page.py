"""Affiche la structure des avis d'une page Trustpilot enregistrée
"""
import json
import sys

from parsel import Selector  # installé avec Scrapy

html = open(sys.argv[1], encoding="utf-8").read()
brut = Selector(text=html).css("script#__NEXT_DATA__::text").get()

if brut is None:
    raise SystemExit("XX Pas de balise __NEXT_DATA__ dans cette page")

page = json.loads(brut)["props"]["pageProps"]
print("Clés disponibles :", list(page.keys()))

avis = page.get("reviews", [])
print(f"{len(avis)} avis trouvés sur la page")
if avis:
    print(json.dumps(avis[0], indent=2, ensure_ascii=False))