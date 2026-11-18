"""Tests de l'anonymisation et de la règle a_garder / a_garder_nlp.

Lancer depuis la racine du projet :   python -m pytest tests -v
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from nettoyage_avis import _anonymiser_reponse, _anonymiser_texte, _nettoyer_texte, traiter_avis  # noqa: E402


# ---------- Réponses d'entreprise : ce qui doit changer ----------
def test_prenom_apres_bonjour():
    r = _anonymiser_reponse("Bonjour Philippe, merci pour votre retour.")
    assert "Philippe" not in r and r.startswith("Bonjour [PRENOM],")


@pytest.mark.parametrize("texte, attendu", [
    ("Bonjour Monsieur Dupont, nous sommes désolés.", "Bonjour Monsieur [NOM], nous sommes désolés."),
    ("Bonjour Mme Martin, merci.", "Bonjour Mme [NOM], merci."),
    ("Cher Paul, merci pour votre avis.", "Cher [PRENOM], merci pour votre avis."),
    ("Chère Marie, bonjour.", "Chère [PRENOM], bonjour."),
    ("Bonjour Jean-Marc, merci.", "Bonjour [PRENOM], merci."),
])
def test_formules_d_appel(texte, attendu):
    assert _anonymiser_reponse(texte) == attendu


@pytest.mark.parametrize("texte, prenom", [
    ("Merci. Julie de l'équipe Cdiscount", "Julie"),
    ("Bien cordialement, Siya", "Siya"),
    ("Merci de votre retour.\nCordialement,\nJulie\nÉquipe de service à la clientèle", "Julie"),
])
def test_signature_remplacee(texte, prenom):
    r = _anonymiser_reponse(texte)
    assert prenom not in r and "L'équipe" in r


def test_donnees_personnelles_dans_reponse():
    r = _anonymiser_reponse("Bonjour Léa, appelez le 06 12 34 56 78 ou écrivez à sav@x.fr, commande n° 12345L12345.")
    for brut in ("Léa", "06 12", "sav@x.fr", "12345L12345"):
        assert brut not in r
    assert "[TEL]" in r and "[EMAIL]" in r and "[NUM]" in r


# ---------- Réponses : ce qui NE doit PAS changer ----------
@pytest.mark.parametrize("texte", [
    "Bonjour, merci pour votre avis.",
    "Bonjour Madame, Monsieur, nous avons bien reçu votre message.",
    "Bonjour à tous, merci.",
    "Bonjour Nous sommes désolés pour ce retard.",
    "Cher Client, merci de votre confiance.",
])
def test_formules_generiques_inchangees(texte):
    assert _anonymiser_reponse(texte) == texte


def test_null_reste_null():
    assert _anonymiser_reponse(None) is None
    assert _anonymiser_reponse(float("nan")) is None
    assert _anonymiser_reponse(pd.NA) is None


def test_idempotence():
    une_fois = _anonymiser_reponse("Bonjour Philippe, merci.\nCordialement,\nJulie")
    assert _anonymiser_reponse(une_fois) == une_fois


# ---------- Textes des clients ----------
@pytest.mark.parametrize("texte, attendu", [
    ("Monsieur Dupont m'a appelé", "Monsieur [NOM] m'a appelé"),
    ("Mme Martin Durand", "Mme [NOM]"),
    ("je m'appelle nathan zerbib et", "je m'appelle [PRENOM] et"),
    ("Bonjour Monsieur, merci", "Bonjour Monsieur, merci"),
    ("Monsieur Le directeur", "Monsieur Le directeur"),
])
def test_noms_dans_textes_clients(texte, attendu):
    assert _anonymiser_texte(texte) == attendu


def test_nettoyer_texte_nfc_et_espaces_insecables():
    s = _nettoyer_texte("café tres bon")      # « e » + accent, espaces insécables
    assert s == "café tres bon" and " " not in s and " " not in s


# ---------- Règles a_garder et a_garder_nlp ----------
def _brut():
    return pd.DataFrame({
        "id": [1, 2, 3, 4],
        "entreprise_id": [10, 10, 10, 10],
        "note": [5, 4, 1, 3],
        "titre": ["Top", "Super", "Nul", "Bof"],
        "texte": ["Très bon service, livraison rapide et produit conforme à la description.",
                  "Phone keeps getting overheated and the battery drains very fast after the update.",
                  "",
                  "pas mal"],
        "date_publication": pd.to_datetime(["2026-01-01"] * 4, utc=True),
        "reponse_texte": ["Bonjour Paul, merci.", None, None, None],
    })


def test_a_garder_est_qualite_seule_et_a_garder_nlp_filtre_la_langue():
    out = traiter_avis(_brut(), verbose=False).set_index("avis_id")
    assert out.loc[1, "a_garder"] and out.loc[1, "a_garder_nlp"]          # français : gardé partout
    assert out.loc[2, "langue"] == "en"
    assert out.loc[2, "a_garder"] and not out.loc[2, "a_garder_nlp"]      # anglais : gardé, mais pas pour le NLP
    assert not out.loc[3, "a_garder"] and not out.loc[3, "a_garder_nlp"]  # texte vide : écarté
    assert out.loc[4, "a_garder"] and out.loc[4, "a_garder_nlp"]          # « pas mal » : gardé


def test_reponse_null_pas_de_chaine_vide_ni_nan():
    out = traiter_avis(_brut(), verbose=False).set_index("avis_id")
    assert out.loc[1, "reponse_propre"] == "Bonjour [PRENOM], merci."
    for i in (2, 3, 4):
        assert out.loc[i, "reponse_propre"] is None


# ---------- Cas trouvés à la relecture manuelle de 50 réponses ----------
@pytest.mark.parametrize("texte, prenom", [
    ("Merci pour votre avis. A bientôt, Violaine, de l'équipe Boulanger.", "Violaine"),
    ("Merci pour votre retour ! À bientôt chez Boulanger ! Charlotte.", "Charlotte"),
    ("[Mise à jour : merci pour vos coordonnées. Bonne fin de journée, Damien] Bonjour Laurence, je comprends.", "Damien"),
    ("[Mise à jour : merci. Bonne fin de journée, Damien] Bonjour Laurence, je comprends.", "Laurence"),
    ("Bonjour sylvie, Nous sommes ravis de lire votre satisfaction.", "sylvie"),
    ("Bonjour Phil49, Je vois en effet que le vendeur n'a pas répondu.", "Phil49"),
    ("Bonjour Catherine et tout d'abord merci pour votre retour.", "Catherine"),
])
def test_fuites_trouvees_a_la_relecture(texte, prenom):
    assert prenom not in _anonymiser_reponse(texte)


@pytest.mark.parametrize("texte", [
    "Nous sommes désolés pour ce retard. Navrés.",
    "Merci pour votre message. Dommage.",
    "Bonjour client, merci pour votre avis.",
    "Bonjour cliente, merci pour votre avis.",
    "Bonjour tous, merci pour vos retours.",
])
def test_mots_courants_pas_pris_pour_des_prenoms(texte):
    assert _anonymiser_reponse(texte) == texte


def test_idempotence_apres_corrections():
    t = "[Mise à jour : Bonne fin de journée, Damien] Bonjour Laurence, merci. A bientôt, Violaine, de l'équipe Boulanger."
    une_fois = _anonymiser_reponse(t)
    assert _anonymiser_reponse(une_fois) == une_fois


def test_signature_sur_sa_propre_ligne_prenom_virgule_de_l_equipe():
    # forme réelle rencontrée dans les données : la signature occupe toute la dernière ligne
    r = _anonymiser_reponse("Nous vous souhaitons une agréable journée, \nViolaine, de l'équipe Boulanger.")
    assert "Violaine" not in r and "L'équipe" in r


@pytest.mark.parametrize("texte, nom", [
    ("[Mise à jour : message envoyé. A bientôt Damien] Le service clients vérifie.", "Damien"),
    ("Merci pour votre confiance. À tout de suite, \nDamien\nJe vous réponds vite.", "Damien"),
    ("Au plaisir de vous revoir chez Boulanger ! Sabrina de l'équipe Boulanger. Bien cordialement.", "Sabrina"),
    ("Bonjour Dutil & Stojanovic, Merci beaucoup.", "Dutil"),
    ("Bonjour Emmanuelle Deudė, Un grand merci.", "Emmanuelle"),
    ("Bonjour Hosene (ou plutôt rebonjour), Je vous réponds.", "Hosene"),
])
def test_derniers_cas_de_la_relecture(texte, nom):
    assert nom not in _anonymiser_reponse(texte)


def test_noms_d_entreprise_conserves():
    r = _anonymiser_reponse("Nous espérons vous revoir chez Boulanger. L'équipe Boulanger")
    assert "Boulanger" in r


@pytest.mark.parametrize("texte", [
    "Je vous ai envoyé une demande en privé. \nA bientôt\nDamien]\n\nLe service clients vérifie.",
    "Merci pour votre réponse ;) Bonne fin de journée, Damien\n\nBonjour Laurent,\nje comprends.",
])
def test_signature_milieu_avec_retours_a_la_ligne(texte):
    assert "Damien" not in _anonymiser_reponse(texte)


# ---------- Cas trouvés dans la vraie base (relecture de 50 réponses + comptages SQL) ----------
@pytest.mark.parametrize("texte, attendu", [
    ("Merci Michel pour votre retour positif.", "Merci [PRENOM] pour votre retour positif."),
    ("Merci, Caroline, pour votre retour positif.", "Merci, [PRENOM], pour votre retour positif."),
    ("Merci Jean Yves pour votre confiance et votre fidélité.", "Merci [PRENOM] pour votre confiance et votre fidélité."),
    ("Merci BG pour votre retour positif.", "Merci [PRENOM] pour votre retour positif."),
    ("Merci Damien pour votre confiance.", "Merci [PRENOM] pour votre confiance."),
])
def test_merci_prenom(texte, attendu):
    assert _anonymiser_reponse(texte) == attendu


@pytest.mark.parametrize("texte", [
    "Merci beaucoup pour votre retour.",
    "Merci Beaucoup pour votre avis.",
    "Merci pour votre retour !",
    "Merci à vous pour votre confiance.",
    "Merci. Nous sommes ravis de votre retour.",
    "Merci Cdiscount pour ce partenariat.",
    "Merci encore pour votre confiance.",
])
def test_merci_sans_prenom_inchange(texte):
    assert _anonymiser_reponse(texte) == texte


@pytest.mark.parametrize("texte, nom", [
    ("We will assist you. Yours sincerely, Niyaz, Customer Advocacy Team.", "Niyaz"),
    ("Thanks for your review. Best regards, Sujit, Customer Advocacy Team.", "Sujit"),
    ("nous sommes ravis de vous satisfaire ; Christine - Equipe Rakuten France", "Christine"),
    ("A bientôt chez Boulanger ! Sabrina de léquipe Boulanger.", "Sabrina"),
    ("Dans les meilleurs délais. Bien à vous, Violaine GAU, de l'équipe Boulanger.", "Violaine"),
    ("Dans les meilleurs délais. Bien à vous, Violaine GAU, de l'équipe Boulanger.", "GAU"),
])
def test_signatures_anglaise_tiret_et_fautes(texte, nom):
    assert nom not in _anonymiser_reponse(texte)


def test_signature_anglaise_garde_le_nom_de_l_equipe():
    r = _anonymiser_reponse("Thanks. Yours sincerely, Niyaz, Customer Advocacy Team.")
    assert "Customer Advocacy Team" in r


def test_idempotence_merci_et_signatures():
    t = "Merci, Caroline, pour votre retour. Yours sincerely, Niyaz, Customer Advocacy Team. Christine - Equipe Rakuten France"
    une_fois = _anonymiser_reponse(t)
    assert _anonymiser_reponse(une_fois) == une_fois