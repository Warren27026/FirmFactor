import pytest

from scripts.download_html import page_path_for
from scripts.parse_downloaded_html import parse_saved_pages


COMPANY = {
    "nom": "Exemple",
    "domaine": "example.com",
    "url_trustpilot": "https://fr.trustpilot.com/review/example.com",
}


def test_extracts_reviews_from_saved_html_without_fetching(tmp_path):
    path = page_path_for(COMPANY, tmp_path)
    path.parent.mkdir()
    path.write_text(
        '<article data-service-review-card-paper data-review-id="avis-1">'
        '<h2 data-service-review-title-typography>Très bien</h2>'
        '<p data-service-review-text-typography>Service utile.</p>'
        '</article>',
        encoding="utf-8",
    )

    items = parse_saved_pages([COMPANY], tmp_path, 100)

    assert len(items) == 1
    assert items[0]["id_externe"] == "avis-1"
    assert items[0]["entreprise"] == "Exemple"
    assert items[0]["titre"] == "Très bien"


def test_missing_html_fails_before_export(tmp_path):
    with pytest.raises(FileNotFoundError, match="HTML manquant"):
        parse_saved_pages([COMPANY], tmp_path, 100)


def test_parses_two_pages_and_deduplicates_reviews(tmp_path):
    first = page_path_for(COMPANY, tmp_path)
    first.parent.mkdir()
    first.write_text(
        '<article data-service-review-card-paper data-review-id="avis-1">'
        '<h2 data-service-review-title-typography>Premier</h2></article>'
        '<a rel="next" href="?page=2">Suivant</a>',
        encoding="utf-8",
    )
    page_path_for(COMPANY, tmp_path, 2).write_text(
        '<article data-service-review-card-paper data-review-id="avis-1">'
        '<h2 data-service-review-title-typography>Premier</h2></article>'
        '<article data-service-review-card-paper data-review-id="avis-2">'
        '<h2 data-service-review-title-typography>Deuxième</h2></article>',
        encoding="utf-8",
    )

    items = parse_saved_pages([COMPANY], tmp_path, 100, max_pages=2)

    assert [item["id_externe"] for item in items] == ["avis-1", "avis-2"]
