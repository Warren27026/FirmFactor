import json
from datetime import date

from bs4 import BeautifulSoup

from scripts.scrape_trustpilot_massif import (
    Settings, collect_six_months, parse_six_month_page, six_month_start,
    six_month_url,
)


URL = "https://fr.trustpilot.com/review/example.com"


def page_html(page, total_pages, total_count, ids, *, per_page=2, star=1):
    reviews = [
        {"id": review_id, "rating": star, "title": "Bien", "text": "Service correct",
         "dates": {"publishedDate": "2026-10-01T10:00:00Z"}}
        for review_id in ids
    ]
    payload = {"props": {"pageProps": {
        "pageUrl": six_month_url(URL, page, star),
        "filters": {"pagination": {
            "currentPage": page, "totalPages": total_pages,
            "totalCount": total_count, "perPage": per_page,
        }},
        "reviews": reviews,
    }}}
    return '<script id="__NEXT_DATA__">' + json.dumps(payload) + "</script>"


def settings_for(tmp_path, *, max_pages=10):
    targets = tmp_path / "entreprises.csv"
    targets.write_text("nom,domaine,secteur,url_trustpilot\nExemple,example.com,commerce," + URL + "\n", encoding="utf-8")
    return Settings(
        csv_path=targets, html_dir=tmp_path / "pages", output_dir=tmp_path / "sortie",
        max_pages=max_pages, offline_only=True, six_months=True,
        snapshot_date=date(2026, 11, 9), star_ratings=(1,),
    )


def test_filter_and_calendar_window():
    assert six_month_start(date(2026, 11, 9)) == date(2026, 5, 9)
    assert six_month_start(date(2026, 8, 31)) == date(2026, 2, 28)
    assert six_month_url(URL, 2) == URL + "?date=last6months&sort=recency&page=2"
    assert six_month_url(URL, 2, 1) == URL + "?date=last6months&sort=recency&stars=1&page=2"
    html = page_html(2, 2, 3, ["a"])
    assert len(parse_six_month_page(BeautifulSoup(html, "html.parser"), six_month_url(URL, 2, 1), 2)[0]) == 1
    wrong = html.replace("last6months", "last12months")
    try:
        parse_six_month_page(BeautifulSoup(wrong, "html.parser"), six_month_url(URL, 2, 1), 2)
    except ValueError:
        pass
    else:
        raise AssertionError("Le filtre absent doit être refusé")


def test_offline_resume_reaches_last_filtered_page(tmp_path):
    settings = settings_for(tmp_path)
    pages = settings.html_dir / "note_1" / "example.com"
    pages.mkdir(parents=True)
    (pages / "page0001.html").write_text(page_html(1, 2, 3, ["a", "b"]), encoding="utf-8")
    assert collect_six_months(settings) == (2, 0)
    assert "needs_download" in (settings.output_dir / "suivi_6mois.csv").read_text(encoding="utf-8-sig")

    (pages / "page0002.html").write_text(page_html(2, 2, 3, ["b", "c"]), encoding="utf-8")
    assert collect_six_months(settings) == (3, 0)
    assert collect_six_months(settings) == (3, 0)
    progress = (settings.output_dir / "suivi_6mois.csv").read_text(encoding="utf-8-sig")
    assert ",complete," in progress
    assert ",2,3,3," in progress
    coverage = (settings.output_dir / "couverture_6mois.csv").read_text(encoding="utf-8-sig")
    assert ",3,3,2,1,complete" in coverage


def test_site_page_cap_is_not_marked_complete(tmp_path):
    settings = settings_for(tmp_path)
    pages = settings.html_dir / "note_1" / "example.com"
    pages.mkdir(parents=True)
    (pages / "page0001.html").write_text(page_html(1, 2, 5, ["a", "b"]), encoding="utf-8")
    (pages / "page0002.html").write_text(page_html(2, 2, 5, ["c", "d"]), encoding="utf-8")
    assert collect_six_months(settings) == (4, 0)
    assert "limite_site" in (settings.output_dir / "suivi_6mois.csv").read_text(encoding="utf-8-sig")
    assert "limite_site" in (settings.output_dir / "couverture_6mois.csv").read_text(encoding="utf-8-sig")


def test_company_complete_only_after_each_rating(tmp_path):
    settings = settings_for(tmp_path)
    settings = Settings(**{**vars(settings), "star_ratings": (1, 5)})
    one = settings.html_dir / "note_1" / "example.com"
    five = settings.html_dir / "note_5" / "example.com"
    one.mkdir(parents=True)
    five.mkdir(parents=True)
    (one / "page0001.html").write_text(page_html(1, 1, 1, ["a"]), encoding="utf-8")
    (five / "page0001.html").write_text(page_html(1, 1, 1, ["b"], star=5), encoding="utf-8")
    assert collect_six_months(settings) == (2, 0)
    assert ",2,2,2,2,complete" in (settings.output_dir / "couverture_6mois.csv").read_text(encoding="utf-8-sig")
