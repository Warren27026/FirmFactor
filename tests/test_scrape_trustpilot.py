import json

from scripts.scrape_trustpilot import collect, parse_page, write_exports


URL = "https://fr.trustpilot.com/review/example.com?sort=recency"


def embedded_page(ids, *, next_page=None):
    reviews = [{
        "id": review_id, "rating": 5, "title": "Titre brut", "text": "Texte brut",
        "dates": {"publishedDate": "2026-11-02T10:00:00Z"},
        "consumer": {"displayName": "Nom privé", "numberOfReviews": 3},
    } for review_id in ids]
    payload = {"props": {"pageProps": {
        "reviews": reviews,
        "filters": {"pagination": {"totalPages": 2 if next_page else 1}},
    }}}
    link = f'<a rel="next" href="?page={next_page}">Suivant</a>' if next_page else ""
    return f'<script id="__NEXT_DATA__">{json.dumps(payload)}</script>{link}'


def test_beautifulsoup_reads_embedded_reviews_without_author():
    _, reviews = parse_page(embedded_page(["a"]), "Exemple", "example.com")
    assert len(reviews) == 1
    assert reviews[0]["id_externe"] == "a"
    assert reviews[0]["note"] == 5
    assert reviews[0]["auteur_nb_avis"] == 3
    assert reviews[0]["date_publication"] == "2026-11-02T10:00:00+00:00"
    assert "Nom privé" not in repr(reviews)


def test_html_fallback_if_embedded_json_is_invalid():
    html = (
        '<script id="__NEXT_DATA__">{broken</script>'
        '<article data-service-review-card-paper="true" data-review-id="b">'
        '<h2>Avis</h2><p data-service-review-text-typography="true">Texte utile</p>'
        '</article>'
    )
    _, reviews = parse_page(html, "Exemple", "example.com")
    assert [item["id_externe"] for item in reviews] == ["b"]


def test_cached_pages_resume_deduplicate_and_export(tmp_path):
    pages = tmp_path / "pages" / "example.com"
    pages.mkdir(parents=True)
    (pages / "page0001.html").write_text(embedded_page(["a", "b"], next_page=2), encoding="utf-8")
    (pages / "page0002.html").write_text(embedded_page(["b", "c"]), encoding="utf-8")
    reviews = collect(URL, "Exemple", "example.com", 3, 2, tmp_path / "pages", offline_only=True)
    assert [item["id_externe"] for item in reviews] == ["a", "b", "c"]
    write_exports(reviews, tmp_path / "exports", "exemple")
    assert len(json.loads((tmp_path / "exports/exemple.json").read_text(encoding="utf-8"))) == 3
    assert len((tmp_path / "exports/exemple.jsonl").read_text(encoding="utf-8").splitlines()) == 3
