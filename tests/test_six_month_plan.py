import csv
from datetime import date

import pytest

from scripts.scrape_trustpilot_massif import save_six_coverage, six_month_searches


def test_planned_searches_keep_global_and_rating_queries_separate(tmp_path):
    targets = [
        {
            "nom": "A", "domaine": "a.example", "secteur": "commerce_en_ligne",
            "url_trustpilot": "https://fr.trustpilot.com/review/a.example?date=last6months&sort=recency",
        },
        {
            "nom": "B", "domaine": "b.example__s1", "secteur": "commerce_en_ligne",
            "url_trustpilot": "https://fr.trustpilot.com/review/b.example?date=last6months&sort=recency&stars=1",
        },
        {
            "nom": "B", "domaine": "b.example__s2", "secteur": "commerce_en_ligne",
            "url_trustpilot": "https://fr.trustpilot.com/review/b.example?date=last6months&sort=recency&stars=2",
        },
    ]
    searches = six_month_searches(targets, True, (1, 2, 3, 4, 5))
    assert [(target["domaine"], star) for target, star in searches] == [
        ("a.example", None), ("b.example__s1", 1), ("b.example__s2", 2),
    ]

    progress = {
        ("a.example", None): {"statut": "complete", "avis_uniques": "3", "avis_annonces": "3", "pages_parcourues": "1"},
        ("b.example__s1", 1): {"statut": "complete", "avis_uniques": "2", "avis_annonces": "2", "pages_parcourues": "1"},
    }
    path = tmp_path / "coverage.csv"
    save_six_coverage(progress, searches, date(2026, 5, 9), date(2026, 11, 9), path)
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 2
    assert rows[0]["domaine"] == "a.example"
    assert rows[0]["statut"] == "complete"
    assert rows[1]["domaine"] == "b.example"
    assert rows[1]["statut"] == "a_faire"
    assert rows[1]["avis_uniques"] == "2"


def test_planned_search_rejects_missing_time_filter():
    target = {
        "nom": "A", "domaine": "a.example", "secteur": "commerce_en_ligne",
        "url_trustpilot": "https://fr.trustpilot.com/review/a.example?sort=recency",
    }
    with pytest.raises(ValueError, match="sans filtre six mois"):
        six_month_searches([target], True, (1, 2, 3, 4, 5))
