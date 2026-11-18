import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from nettoyage_avis import _anonymiser_texte  # noqa: E402


@pytest.mark.parametrize("brut, attendu", [
    # Téléphones
    ("appelé le service client au 01 23 45 67 89 hier", "appelé le service client au [TEL] hier"),
    ("mon numéro 06.12.34.56.78", "mon numéro [TEL]"),
    ("joignable au +33 6 12 34 56 78.", "joignable au [TEL]."),
    ("tel 0612345678", "tel [TEL]"),
    # Commandes, factures, références
    ("commande n° 1234567890123456 jamais reçue", "commande n° [NUM] jamais reçue"),
    ("Facture N°2023000123456", "Facture N°[NUM]"),
    ("ma commande 98765 est bloquée", "ma commande [NUM] est bloquée"),
    ("référence : AB-12345", "référence : [NUM]"),
    ("le n° 12345L12345 n'existe pas", "le n° [NUM] n'existe pas"),
    ("carte 1234 5678 9012 3456", "carte [NUM]"),
    # Suivi de colis
    ("colis LA123456789FR perdu", "colis [SUIVI] perdu"),
    ("numéro 6A12345678901 introuvable", "numéro [SUIVI] introuvable"),
    ("numéro de suivi : XY12345678", "numéro de suivi : [SUIVI]"),
    # IBAN
    ("rembourser sur FR76 3000 6000 0112 3456 7890 189 svp", "rembourser sur [IBAN] svp"),
    # Restes de 8 chiffres ou plus
    ("dossier ouvert, ref interne 12345678", "dossier ouvert, ref interne [NUM]"),
    ("code 99887766554", "code [NUM]"),
])
def test_donnees_personnelles_masquees(brut, attendu):
    assert _anonymiser_texte(brut) == attendu


@pytest.mark.parametrize("texte", [
    "Livraison rapide, très bien",
    "commande de 120 euros reçue en 2 jours",
    "client depuis 2015, toujours satisfait",
    "le suivi de ma commande était clair",
    "2 allée des roses, 75001 Paris",          # adresses / codes postaux : hors périmètre
    "reçu le 12/03/2024 à 14h30",
    "facture 2023",
])
def test_texte_ordinaire_inchange(texte):
    assert _anonymiser_texte(texte) == texte


def test_vide():
    assert _anonymiser_texte("") == ""


# ---------------------------------------------------------------------
#  Noms dans les avis clients
# ---------------------------------------------------------------------
@pytest.mark.parametrize("brut, attendu", [
    ("j'ai eu Monsieur Dupont au téléphone", "j'ai eu Monsieur [NOM] au téléphone"),
    ("Mme Martin Durand a été parfaite", "Mme [NOM] a été parfaite"),
    ("je m'appelle Jules et je suis déçu", "je m'appelle [PRENOM] et je suis déçu"),
    ("je m'appelle nathan zerbib", "je m'appelle [PRENOM]"),
    ("écrire à jean.dupont@gmail.com", "écrire à [EMAIL]"),
    ("voir https://exemple.fr/suivi?id=3.", "voir [URL]."),
])
def test_noms_et_contacts_clients(brut, attendu):
    assert _anonymiser_texte(brut) == attendu


@pytest.mark.parametrize("texte", [
    "Madame, Monsieur, je vous écris",
    "Monsieur le directeur, merci",
])
def test_civilite_sans_nom_inchangee(texte):
    assert _anonymiser_texte(texte) == texte


# ---------------------------------------------------------------------
#  Réponses des entreprises
# ---------------------------------------------------------------------
from nettoyage_avis import _anonymiser_reponse, _nettoyer_texte  # noqa: E402


@pytest.mark.parametrize("brut, attendu", [
    ("Bonjour Philippe, merci pour votre retour.", "Bonjour [PRENOM], merci pour votre retour."),
    ("Bonjour Monsieur Dupont, merci.", "Bonjour Monsieur [NOM], merci."),
    ("Bonjour Mme Martin,\nNous sommes désolés.", "Bonjour Mme [NOM], Nous sommes désolés."),
    ("Cher Paul,\nMerci !", "Cher [PRENOM], Merci !"),
    ("Chère Marie, merci.", "Chère [PRENOM], merci."),
    ("Bonjour Gilles Champalbert,\n\nNavrés.", "Bonjour [PRENOM], Navrés."),
    ("Bonjour  Nathalie,\n\nMerci.", "Bonjour [PRENOM], Merci."),
    ("Bonjour Patrick,\n\nMerci.", "Bonjour [PRENOM], Merci."),     # espace fine insécable
    ("Bonjour Le,\n\nNous vous remercions.", "Bonjour [PRENOM], Nous vous remercions."),
    ("Cher Client,\nMerci.", "Cher Client, Merci."),
    ("Bonjour Madame,\nMerci.", "Bonjour Madame, Merci."),
    # Rien à retirer
    ("Bonjour, merci pour votre avis.", "Bonjour, merci pour votre avis."),
    ("Cher client,\nMerci pour votre avis.", "Cher client, Merci pour votre avis."),
    ("Bonjour Nous sommes désolés, vraiment.", "Bonjour Nous sommes désolés, vraiment."),
    ("Bonjour,\n\nMerci.\n\nL'équipe Voyage Privé", "Bonjour, Merci. L'équipe Voyage Privé"),
    ("Merci.\n\nCordialement,\nL’équipe ColisExpat", "Merci. Cordialement, L’équipe ColisExpat"),
    ("Merci.\n\nÀ votre service !", "Merci. À votre service !"),
    ("Merci.\n\nKind regards,\nCustomer Advocacy Team", "Merci. Kind regards, Customer Advocacy Team"),
    # Signatures
    ("Merci. Julie de l'équipe Cdiscount", "Merci. L'équipe"),
    ("Merci.\n\nCordialement,\nSabrina de l'équipe Boulanger.", "Merci. Cordialement, L'équipe"),
    ("Merci.\n\nBien cordialement,\nNelson,  Service Client Back Market", "Merci. Bien cordialement, L'équipe"),
    ("Merci.\nOlivia, Manager Service Client Back Market", "Merci. L'équipe"),
    ("Merci.\n\nBonne journée,\n\nNelson de Back Market", "Merci. Bonne journée, L'équipe"),
    ("Merci à vous.\n\nLéa, Promovacances", "Merci à vous. L'équipe"),
    ("Merci.\n\nÀ bientôt et bonne journée,\nDamien", "Merci. À bientôt et bonne journée, L'équipe"),
    ("Merci.\n\nSincèrement,\n\nABDOU\n\nÉquipe de service à la clientèle",
     "Merci. Sincèrement, L'équipe Équipe de service à la clientèle"),
    ("Merci.\n\nCordialement, Siya", "Merci. Cordialement, L'équipe"),
    # Une dernière phrase ordinaire n'est pas une signature
    ("Bonjour,\nMalheureusement, ce n'est pas possible.", "Bonjour, Malheureusement, ce n'est pas possible."),
    # Masquage des numéros aussi dans les réponses
    ("Bonjour, appelez le 01 23 45 67 89 ou ecrivez a sav@exemple.fr.",
     "Bonjour, appelez le [TEL] ou ecrivez a [EMAIL]."),
])
def test_reponses_anonymisees(brut, attendu):
    assert _anonymiser_reponse(brut) == attendu


@pytest.mark.parametrize("vide", [None, float("nan")])
def test_reponse_absente_reste_nulle(vide):
    assert _anonymiser_reponse(vide) is None


def test_nettoyer_texte_nfc_et_insecables():
    decompose = "été ok !"          # « été » écrit e + accent
    assert _nettoyer_texte(decompose) == "été ok !"


# ---------------------------------------------------------------------
#  traiter_avis : nouvelles colonnes
# ---------------------------------------------------------------------
def test_traiter_avis_colonnes():
    import pandas as pd
    from nettoyage_avis import traiter_avis

    df = pd.DataFrame({
        "id": [1, 2, 3],
        "entreprise_id": [1, 1, 1],
        "titre": ["Livraison très rapide et…", "Top", "Very good"],
        "texte": ["Livraison très rapide et produit conforme, merci Monsieur Dupont",
                  "parfait", "Very good service, I will order again for sure"],
        "date_publication": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"], utc=True),
        "reponse_texte": ["Bonjour Paul,\nMerci !\nEmma", None, None],
    })
    out = traiter_avis(df, verbose=False).set_index("avis_id")
    assert out.loc[1, "texte_complet"] == "Livraison très rapide et produit conforme, merci Monsieur [NOM]"
    assert out.loc[1, "titre_tronque"]
    assert out.loc[1, "reponse_propre"] == "Bonjour [PRENOM], Merci ! L'équipe"
    assert out.loc[2, "reponse_propre"] is None
    assert out.loc[2, "texte_court"] and out.loc[2, "a_garder"]       # langue NULL : gardé
    assert out.loc[3, "langue"] == "en" and not out.loc[3, "a_garder"]
    assert out.loc[2, "texte_complet"] == "Top. parfait"
