import json

import httpx

from scripts.download_html import page_path_for
from scripts.scrape_trustpilot_csv import (
    USER_AGENT,
    collect_company,
    robots_for,
    write_exports,
)


URL = "https://fr.trustpilot.com/review/example.com"
COMPANY = {"nom": "Exemple", "domaine": "example.com", "url_trustpilot": URL}


def test_robots_denial_prevents_page_download(tmp_path):
    requested = []

    def handler(request):
        requested.append(str(request.url))
        return httpx.Response(200, text="User-agent: *\nDisallow: /\n")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        robots = robots_for(client, URL)
        assert not robots.can_fetch(USER_AGENT, URL)
        assert collect_company(client, COMPANY, tmp_path, robots, 20, 2, 0) == []
        assert requested == ["https://fr.trustpilot.com/robots.txt"]
        assert not tmp_path.joinpath("html").exists()


def test_allowed_pages_are_saved_and_reviews_exported(tmp_path):
    reviews = [{
        "id": "avis-1", "rating": 5, "title": "Bien", "text": "Utile",
        "dates": {"publishedDate": "2026-10-20T10:00:00Z"},
    }]
    html = (
        '<script id="__NEXT_DATA__">'
        + json.dumps({"props": {"pageProps": {"reviews": reviews}}})
        + "</script>"
    )

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        return httpx.Response(200, text=html)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        robots = robots_for(client, URL)
        items = collect_company(client, COMPANY, tmp_path, robots, 20, 2, 0, tmp_path / "pages")

    assert [item["id_externe"] for item in items] == ["avis-1"]
    assert page_path_for(COMPANY, tmp_path / "pages").read_text() == html
    write_exports(items, tmp_path)
    assert json.loads(tmp_path.joinpath("trustpilot.json").read_text())[0]["note"] == 5
    assert "avis-1" in tmp_path.joinpath("trustpilot.csv").read_text(encoding="utf-8-sig")
    assert len(tmp_path.joinpath("trustpilot.jsonl").read_text().splitlines()) == 1
