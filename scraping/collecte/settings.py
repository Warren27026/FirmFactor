"""Réglages du scraper FirmFactor."""
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True))  # charge le .env à la racine du projet

BOT_NAME = "collecte"
SPIDER_MODULES = ["collecte.spiders"]
NEWSPIDER_MODULE = "collecte.spiders"

# Cadre validé par l'encadrant : exception de fouille de textes et de données
# pour la recherche scientifique (art. L. 122-5-3 II du CPI).
ROBOTSTXT_OBEY = False

# On s'identifie honnêtement
USER_AGENT = "FirmFactor-ENSIM/1.0 (projet de recherche etudiant, ENSIM Le Mans)"

# Collecte lente et polie : 1 requête à la fois, ~3 s entre deux pages
CONCURRENT_REQUESTS_PER_DOMAIN = 1
DOWNLOAD_DELAY = 3
DOWNLOAD_DELAY_JITTER = 0.5
AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 3
AUTOTHROTTLE_MAX_DELAY = 30
RETRY_HTTP_CODES = [429, 500, 502, 503, 504]

# En développement : une page déjà téléchargée n'est pas redemandée au site
HTTPCACHE_ENABLED = True
HTTPCACHE_EXPIRATION_SECS = 24 * 3600
HTTPCACHE_IGNORE_HTTP_CODES = [403, 429, 500, 502, 503, 504]

ITEM_PIPELINES = {"collecte.pipelines.SupabasePipeline": 300}
FEED_EXPORT_ENCODING = "utf-8"