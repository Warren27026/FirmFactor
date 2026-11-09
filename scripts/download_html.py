"""Enregistre le HTML rendu des URL de scraping/entreprises.csv avec Chrome.

Installation dans Colab (cellules de commande) :
    !apt-get update
    !apt-get install -y google-chrome-stable
    !pip install selenium webdriver-manager

Depuis la racine du projet, dans Colab ou en local :
    python -m scripts.download_html --max-entreprises 1

Les fichiers sont enregistrés dans data/pages/<domaine>/ (ignoré par Git). Une page de
blocage n'est pas enregistrée comme une page d'avis.
"""

from __future__ import annotations

import argparse
import csv
import random
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, quote, urljoin, urlparse

from bs4 import BeautifulSoup
from selenium.common.exceptions import WebDriverException


ROOT = Path(__file__).resolve().parents[1]
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
REVIEW_MARKER = "data-service-review-card-paper"


def is_trustpilot_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and (
        parsed.hostname == "trustpilot.com"
        or (parsed.hostname or "").endswith(".trustpilot.com")
    )


def read_companies(path: Path, maximum: int | None) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames or not {"nom", "domaine", "url_trustpilot"}.issubset(
            reader.fieldnames
        ):
            raise ValueError("Colonnes requises : nom, domaine, url_trustpilot")
        companies = list(reader)

    if maximum is not None:
        companies = companies[:maximum]
    for number, company in enumerate(companies, start=2):
        if not is_trustpilot_url(company["url_trustpilot"]):
            raise ValueError(f"Ligne {number} : URL Trustpilot invalide")
        if not company["domaine"].strip():
            raise ValueError(f"Ligne {number} : domaine manquant")
    return companies


def html_path_for(
    company: dict[str, str], index: int, output_dir: Path, page: int = 1
) -> Path:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", company["domaine"]).strip("_")
    suffix = "" if page == 1 else f"-page-{page}"
    return output_dir / f"{index:03d}-{slug}{suffix}.html"


def page_path_for(company: dict[str, str], output_dir: Path, page: int = 1) -> Path:
    """Chemin stable des pages brutes FirmFactor."""
    domain = company["domaine"]
    if not domain or domain in {".", ".."} or page < 1:
        raise ValueError(f"Domaine ou page invalide : {domain!r}, {page}")
    return output_dir / quote(domain, safe="") / f"page{page:04d}.html"


def next_page_url(html: str, current_url: str, base_url: str, page: int) -> str | None:
    link = BeautifulSoup(html, "html.parser").select_one('a[rel="next"][href]')
    href = str(link["href"]) if link else None
    if not href:
        return None
    url = urljoin(current_url, href)
    parsed = urlparse(url)
    base = urlparse(base_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != base.hostname
        or parsed.path.rstrip("/") != base.path.rstrip("/")
        or parse_qs(parsed.query).get("page") != [str(page)]
    ):
        raise ValueError(f"Lien de pagination inattendu : {url}")
    return url


def create_driver(chrome_binary: Path | None = None, profile_dir: Path | None = None):
    """Démarre Chrome ; webdriver-manager fournit le ChromeDriver associé."""
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from webdriver_manager.chrome import ChromeDriverManager

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument(f"--user-agent={USER_AGENT}")
    if profile_dir is not None:
        profile_dir = profile_dir.resolve()
        if not profile_dir.is_dir():
            raise FileNotFoundError(
                f"Profil Chrome introuvable : {profile_dir}. Ouvrez Chrome avec ce profil et connectez-vous d'abord."
            )
        options.add_argument(f"--user-data-dir={profile_dir}")
    options.page_load_strategy = "eager"
    if chrome_binary is not None:
        if not chrome_binary.is_file():
            raise FileNotFoundError(f"Binaire Chrome introuvable : {chrome_binary}")
        options.binary_location = str(chrome_binary)
    elif Path("/usr/bin/google-chrome").is_file():
        options.binary_location = "/usr/bin/google-chrome"

    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()), options=options
    )
    chrome_major = str(driver.capabilities.get("browserVersion", "")).split(".")[0]
    if chrome_major.isdigit():
        user_agent = re.sub(r"Chrome/\d+", f"Chrome/{chrome_major}", USER_AGENT)
        driver.execute_cdp_cmd("Network.setUserAgentOverride", {"userAgent": user_agent})
    driver.set_page_load_timeout(30)
    return driver


def download_pages(
    companies: list[dict[str, str]],
    output_dir: Path,
    wait_min: float,
    wait_max: float,
    driver_factory,
    max_pages: int = 1,
) -> int:
    saved = 0
    failed = []
    for index, company in enumerate(companies, start=1):
        url = company["url_trustpilot"]
        for page in range(1, max_pages + 1):
            destination = page_path_for(company, output_dir, page)
            html = None
            if destination.exists():
                existing = destination.read_text(encoding="utf-8")
                if REVIEW_MARKER in existing:
                    print(f"Déjà présent : {destination}")
                    html = existing
                else:
                    print(f"Fichier sans avis, nouvel essai : {destination}")

            if html is None:
                for attempt in range(1, 3):
                    driver = None
                    try:
                        print(f"Ouverture : {company['nom']} page {page} ({url}), essai {attempt}")
                        driver = driver_factory()
                        driver.get(url)
                        time.sleep(random.uniform(wait_min, wait_max))
                        final_url = driver.current_url
                        if not is_trustpilot_url(final_url):
                            raise ValueError(f"Redirection inattendue vers {final_url}")

                        candidate = driver.page_source
                        title = driver.title.lower()
                        if not candidate or REVIEW_MARKER not in candidate or any(
                            marker in title
                            for marker in (
                                "access denied", "forbidden", "captcha", "verifying connection"
                            )
                        ):
                            print(f"Page sans avis ou page de blocage pour {url}")
                            continue

                        destination.parent.mkdir(parents=True, exist_ok=True)
                        destination.write_text(candidate, encoding="utf-8")
                        html = candidate
                        saved += 1
                        print(f"Enregistré : {destination}")
                        break
                    except WebDriverException as exc:
                        print(f"Échec du navigateur pour {company['nom']} : {exc.msg}")
                    finally:
                        if driver is not None:
                            try:
                                driver.quit()
                            except WebDriverException:
                                pass
                if html is None:
                    failed.append(f"{company['nom']} page {page}")
                    break

            if page < max_pages:
                next_url = next_page_url(html, url, company["url_trustpilot"], page + 1)
                if next_url is None:
                    print(f"Dernière page pour {company['nom']} : {page}")
                    break
                url = next_url

    print(f"{saved} page(s) HTML enregistrée(s) dans {output_dir}")
    if failed:
        print(f"Échecs après deux essais : {', '.join(failed)}")
        return 2
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=ROOT / "scraping/entreprises.csv")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/pages")
    parser.add_argument("--max-entreprises", type=int)
    parser.add_argument("--max-pages", type=int, default=1)
    parser.add_argument("--wait-min", type=float, default=4.0)
    parser.add_argument("--wait-max", type=float, default=6.0)
    parser.add_argument("--chrome-binary", type=Path, help="Chemin du binaire Google Chrome")
    args = parser.parse_args()
    if args.max_entreprises is not None and args.max_entreprises < 1:
        parser.error("--max-entreprises doit être positif")
    if args.max_pages < 1:
        parser.error("--max-pages doit être positif")
    if args.wait_min < 0 or args.wait_max < args.wait_min:
        parser.error("Intervalle d'attente invalide")

    try:
        companies = read_companies(args.csv, args.max_entreprises)
        return download_pages(
            companies,
            args.output_dir,
            args.wait_min,
            args.wait_max,
            lambda: create_driver(args.chrome_binary),
            args.max_pages,
        )
    except (OSError, ValueError, ImportError, WebDriverException) as exc:
        print(f"Arrêt : {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
