import csv
import json
from datetime import date, datetime

import pandas as pd

from scripts import reprendre_collecte_6mois as reprise
from scripts.scrape_trustpilot_massif import REVIEW_FIELDS, save_reviews


def test_reprise_keeps_only_fixed_window_and_deduplicates(tmp_path, monkeypatch):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls):
            return cls(2026, 11, 10, 10, 0)

    monkeypatch.setattr(reprise, "ROOT", tmp_path)
    monkeypatch.setattr(reprise, "datetime", FrozenDateTime)
    monkeypatch.setattr(reprise.time, "sleep", lambda _: None)

    output = tmp_path / "data/collectes_6mois_plan/2026-11-09"
    output.mkdir(parents=True)
    save_reviews(pd.DataFrame(columns=REVIEW_FIELDS), output / "avis_6mois.csv")
    targets = tmp_path / "targets.csv"
    with targets.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["nom", "secteur", "domaine", "url_trustpilot"])
        writer.writeheader()
        writer.writerow({
            "nom": "A", "secteur": "commerce_en_ligne", "domaine": "a.example",
            "url_trustpilot": "https://fr.trustpilot.com/review/a.example?date=last6months&sort=recency",
        })

    calls = []

    def fake_fetch(url, base, page, chrome_binary, profile_dir, require_reviews):
        calls.append(url)
        payload = {
            "props": {"pageProps": {
                "pageUrl": url,
                "filters": {"pagination": {"currentPage": 1, "totalPages": 3, "totalCount": 3, "perPage": 20}},
                "reviews": [
                    {"id": "new", "rating": 5, "dates": {"publishedDate": "2026-11-10T09:00:00Z"}},
                    {"id": "inside", "rating": 4, "dates": {"publishedDate": "2026-11-09T09:00:00Z"}},
                    {"id": "old", "rating": 1, "dates": {"publishedDate": "2026-05-08T09:00:00Z"}},
                ],
            }},
        }
        return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(payload)}</script>'

    monkeypatch.setattr(reprise, "fetch_html", fake_fetch)
    total, failures = reprise.collect(date(2026, 11, 9), targets, 100, tmp_path)
    assert (total, failures) == (1, 0)
    assert len(calls) == 1
    assert "date=last12months" in calls[0]
    with (output / "avis_6mois.csv").open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert [row["id_externe"] for row in rows] == ["inside"]
    with (output / "suivi_reprise.csv").open(encoding="utf-8-sig", newline="") as stream:
        state = next(csv.DictReader(stream))
    assert state["statut"] == "complete"
