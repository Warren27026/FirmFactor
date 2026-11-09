import json
from datetime import date, timezone
from decimal import Decimal

import pytest

from scripts.importer_pages import SECTEURS, extraire_avis, extraire_page, importer
from scripts.migrer_pages import migrer


def exemple_html(*, review_id="avis-1", rating=5):
    payload = {
        "props": {"pageProps": {
            "businessUnit": {"trustScore": 4.1, "numberOfReviews": 289090},
            "reviews": [{
                "id": review_id, "rating": rating, "title": "Titre brut",
                "text": "Texte brut", "dates": {
                    "publishedDate": "2026-11-02T14:12:12.000Z",
                    "experiencedDate": "2026-11-01T00:00:00.000Z",
                },
                "labels": {"verification": {"isVerified": False}},
                "reply": None,
                "consumer": {"displayName": "Nom privé", "id": "profil-1", "numberOfReviews": 5},
            }],
        }}
    }
    return '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(payload) + "</script>"


def test_mapping_json_et_minimisation():
    metrics, reviews = extraire_page(exemple_html())
    assert metrics == {"note_trustpilot": Decimal("4.1"), "nb_avis_affiche": 289090}
    assert reviews[0] == {
        "id_externe": "avis-1", "note": 5, "titre": "Titre brut", "texte": "Texte brut",
        "date_publication": reviews[0]["date_publication"],
        "date_experience": date(2026, 11, 1), "verifie": False,
        "reponse_texte": None, "reponse_date": None, "auteur_nb_avis": 5,
    }
    assert reviews[0]["date_publication"].tzinfo is timezone.utc
    assert "Nom privé" not in repr(reviews)
    assert set(SECTEURS.values()) == {"commerce_en_ligne", "voyages", "livraison_colis"}


def test_note_invalide_et_date_sans_fuseau():
    with pytest.raises(ValueError, match="Note"):
        extraire_page(exemple_html(rating=6))
    with pytest.raises(ValueError, match="Date sans fuseau"):
        extraire_avis({"id": "x", "rating": 5, "dates": {"publishedDate": "2026-11-02T12:00:00"}})


def test_import_hors_ligne_et_migration(tmp_path):
    csv_path = tmp_path / "entreprises.csv"
    csv_path.write_text(
        "nom,domaine,secteur,url_trustpilot\nExemple,example.com,commerce,https://fr.trustpilot.com/review/example.com\n",
        encoding="utf-8",
    )
    ancien = tmp_path / "html"
    ancien.mkdir()
    (ancien / "001-example_com.html").write_text(exemple_html(), encoding="utf-8")
    nouveau = tmp_path / "pages"
    assert migrer(csv_path, ancien, nouveau) == 1
    assert (nouveau / "example.com/page0001.html").is_file()
    from scripts.scrape_trustpilot_massif import read_targets
    assert importer(read_targets(csv_path), nouveau) == (1, 1, 0, 0)
