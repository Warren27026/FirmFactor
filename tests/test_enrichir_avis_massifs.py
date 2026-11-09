import json

import pandas as pd

from scripts.enrichir_avis_massifs import enrichir
from scripts.scrape_trustpilot_massif import REVIEW_FIELDS


def test_enrichir_preserves_rows_and_missing_values(tmp_path):
    pages = tmp_path / "pages" / "example.com"
    pages.mkdir(parents=True)
    payload = {"props": {"pageProps": {
        "businessUnit": {"trustScore": 4.1, "numberOfReviews": 10},
        "reviews": [{
            "id": "r1", "rating": 5,
            "dates": {"publishedDate": "2026-11-02T10:00:00Z", "experiencedDate": "2026-11-01T00:00:00Z"},
            "labels": {"verification": {"isVerified": False}},
            "reply": None,
            "consumer": {"displayName": "Nom privé", "numberOfReviews": 3},
        }],
    }}}
    (pages / "page0001.html").write_text(
        '<script id="__NEXT_DATA__">' + json.dumps(payload) + "</script>",
        encoding="utf-8",
    )
    frame = pd.DataFrame([
        {"domaine": "example.com", "id_externe": "r1", "texte": "Texte brut"},
        {"domaine": "example.com", "id_externe": "r2", "texte": "Autre avis"},
    ]).reindex(columns=REVIEW_FIELDS, fill_value="")

    result, matched, valid_pages, errors = enrichir(frame, tmp_path / "pages")

    assert (matched, valid_pages, errors) == (1, 1, 0)
    assert len(result) == 2
    assert result.loc[0, "date_experience"] == "2026-11-01"
    assert result.loc[0, "verifie"] == "false"
    assert result.loc[0, "reponse_texte"] == ""
    assert result.loc[0, "auteur_nb_avis"] == "3"
    assert result.loc[1, "date_experience"] == ""
    assert result.loc[1, "texte"] == "Autre avis"
    assert "Nom privé" not in result.to_csv(index=False)
