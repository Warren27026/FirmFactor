import json

from bs4 import BeautifulSoup

from scripts.scrape_trustpilot_massif import Settings, collect, next_page_url, parse_articles


URL = "https://fr.trustpilot.com/review/example.com"


def card(review_id: str, text: str, *, user_id="user-1") -> str:
    return f"""
    <article data-service-review-card-paper="true">
      <div data-testid="service-review-card-v2">
        <a href="/users/{user_id}"><span data-consumer-name-typography="true">Alice</span></a>
        <time data-service-review-date-time-ago="true" datetime="2026-11-02T10:00:00Z"></time>
        <img alt="Noté 5 sur 5 étoiles">
        <h2>Bon service</h2>
        <p data-service-review-text-typography="true">{text}</p>
        <a href="/reviews/{review_id}">Partager</a>
      </div>
    </article>
    """


def test_parse_articles_excludes_carousel_and_uses_review_id():
    carousel = (
        '<article data-service-review-card-paper="true" class="carouselReviewCard">'
        '<h2>Carrousel</h2></article>'
        '<article data-service-review-card-paper="true" class="styles_reviewCard__meSdm">'
        '<a href="/users/user-2">Profil</a><h2>Carrousel</h2></article>'
    )
    soup = BeautifulSoup(carousel + card("review-1", "Un texte utile."), "html.parser")

    reviews = parse_articles(soup)

    assert len(reviews) == 1
    assert reviews[0] == {
        "id_externe": "review-1",
        "date_publication": "2026-11-02T10:00:00Z",
        "note": 5,
        "titre": "Bon service",
        "texte": "Un texte utile.",
        "date_experience": None,
        "verifie": None,
        "reponse_texte": None,
        "reponse_date": None,
        "auteur_nb_avis": None,
    }


def test_parse_articles_recovers_supplement_from_json():
    payload = {"props": {"pageProps": {"reviews": [{
        "id": "review-1", "rating": 5,
        "dates": {"experiencedDate": "2026-11-01T00:00:00.000Z"},
        "labels": {"verification": {"isVerified": False}},
        "reply": {"message": "Merci", "publishedDate": "2026-11-03T09:00:00Z"},
        "consumer": {"displayName": "Nom privé", "numberOfReviews": 7},
    }]}}}
    soup = BeautifulSoup(
        card("review-1", "Un texte utile.")
        + '<script id="__NEXT_DATA__" type="application/json">'
        + json.dumps(payload) + "</script>",
        "html.parser",
    )
    review = parse_articles(soup)[0]
    assert str(review["date_experience"]) == "2026-11-01"
    assert review["verifie"] is False
    assert review["reponse_texte"] == "Merci"
    assert review["reponse_date"].isoformat() == "2026-11-03T09:00:00+00:00"
    assert review["auteur_nb_avis"] == 7
    assert "Nom privé" not in repr(review)


def test_pagination_uses_next_data_when_link_disappears():
    html = '<script id="__NEXT_DATA__">' + json.dumps({
        "props": {"pageProps": {"filters": {"pagination": {"totalPages": 30}}}}
    }) + "</script>"
    soup = BeautifulSoup(html, "html.parser")
    assert next_page_url(soup, URL + "?page=10", URL, 11) == URL + "?page=11"


def test_offline_checkpoint_resume_and_exact_quota(tmp_path):
    csv_path = tmp_path / "entreprises.csv"
    csv_path.write_text("nom,url_trustpilot\nExemple," + URL + "\n", encoding="utf-8")
    html_dir = tmp_path / "html"
    html_dir.mkdir()
    company_dir = html_dir / "example.com"
    company_dir.mkdir()
    (company_dir / "page0001.html").write_text(
        card("review-1", "Premier avis.")
        + '<a rel="next" href="?page=2">Suivant</a>',
        encoding="utf-8",
    )
    settings = Settings(
        csv_path=csv_path, html_dir=html_dir, output_dir=tmp_path / "output",
        quota=2, max_pages=2, offline_only=True,
    )

    assert collect(settings) == (1, 0)
    progress = (settings.output_dir / "scraping_progress_massif.csv").read_text(
        encoding="utf-8-sig"
    )
    assert "needs_download" in progress

    (company_dir / "page0002.html").write_text(
        card("review-1", "Premier avis.")
        + card("review-2", "Deuxième avis.")
        + card("review-3", "Troisième avis."),
        encoding="utf-8",
    )
    (settings.output_dir / "scraping_progress_massif.csv").write_text(
        progress.replace("needs_download", "complete"), encoding="utf-8-sig"
    )
    assert collect(settings) == (2, 0)
    assert collect(settings) == (2, 0)
    reviews = (settings.output_dir / "avis_massifs.csv").read_text(encoding="utf-8-sig")
    assert "review-2" in reviews
    assert "review-3" not in reviews
