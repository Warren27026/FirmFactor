from selenium.common.exceptions import TimeoutException
import pytest

from scripts.download_html import (
    REVIEW_MARKER,
    create_driver,
    download_pages,
    page_path_for,
    next_page_url,
    read_companies,
)


URL = "https://fr.trustpilot.com/review/example.com"
COMPANY = {"nom": "Exemple", "domaine": "example.com", "url_trustpilot": URL}


def test_profile_must_exist_before_starting_chrome(tmp_path):
    with pytest.raises(FileNotFoundError, match="Profil Chrome introuvable"):
        create_driver(profile_dir=tmp_path / "profil_absent")


class FakeDriver:
    def __init__(
        self, *, title="Exemple - Trustpilot", html=f"<html>{REVIEW_MARKER}</html>"
    ):
        self.current_url = URL
        self.title = title
        self.page_source = html
        self.visited = []

    def get(self, url):
        self.visited.append(url)
        self.current_url = url

    def quit(self):
        pass


def test_csv_is_read_with_default_column(tmp_path):
    csv_path = tmp_path / "entreprises.csv"
    csv_path.write_text(
        "nom,domaine,url_trustpilot\nExemple,example.com," + URL + "\n",
        encoding="utf-8",
    )
    assert read_companies(csv_path, None) == [COMPANY]


def test_rendered_html_is_saved(tmp_path):
    driver = FakeDriver()
    assert download_pages([COMPANY], tmp_path, 0, 0, lambda: driver) == 0
    assert driver.visited == [URL]
    assert page_path_for(COMPANY, tmp_path).read_text(encoding="utf-8") == (
        f"<html>{REVIEW_MARKER}</html>"
    )


def test_block_page_is_not_saved(tmp_path):
    driver = FakeDriver(title="Access Denied", html="<html>Forbidden</html>")
    assert download_pages([COMPANY], tmp_path, 0, 0, lambda: driver) == 2
    assert list(tmp_path.iterdir()) == []


def test_existing_verification_page_is_replaced(tmp_path):
    destination = page_path_for(COMPANY, tmp_path)
    destination.parent.mkdir()
    destination.write_text("<html>Verifying Connection</html>", encoding="utf-8")
    assert download_pages([COMPANY], tmp_path, 0, 0, FakeDriver) == 0
    assert REVIEW_MARKER in destination.read_text(encoding="utf-8")


def test_timeout_retries_with_new_driver(tmp_path):
    class TimedOutDriver(FakeDriver):
        def get(self, url):
            raise TimeoutException("chargement trop lent")

    drivers = iter([TimedOutDriver(), FakeDriver()])
    assert download_pages([COMPANY], tmp_path, 0, 0, lambda: next(drivers)) == 0
    assert page_path_for(COMPANY, tmp_path).exists()


def test_downloads_second_page_from_saved_next_link(tmp_path):
    first = page_path_for(COMPANY, tmp_path)
    first.parent.mkdir()
    first.write_text(
        f'<html>{REVIEW_MARKER}<a rel="next" href="?page=2">Suivant</a></html>',
        encoding="utf-8",
    )
    driver = FakeDriver()
    assert download_pages([COMPANY], tmp_path, 0, 0, lambda: driver, max_pages=2) == 0
    assert driver.visited == [URL + "?page=2"]
    assert page_path_for(COMPANY, tmp_path, 2).exists()


def test_rejects_pagination_to_another_company():
    html = '<a rel="next" href="/review/other.com?page=2">Suivant</a>'
    try:
        next_page_url(html, URL, URL, 2)
    except ValueError:
        pass
    else:
        raise AssertionError("Le lien vers une autre entreprise doit être rejeté")
