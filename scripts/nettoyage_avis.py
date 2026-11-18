"""Nettoyage des avis (FirmFactor) - fonction réutilisable.

Deux usages :
  1) Fichier :   python nettoyage_avis.py data/raw/avis.csv data/clean/avis_nettoyes.csv
  2) Importée par pipeline_supabase.py : from nettoyage_avis import nettoyer

Le brut n'est jamais modifié : on produit un nouveau tableau.
"""
import hashlib
import re
import sys
import unicodedata

import pandas as pd

OBLIGATOIRES = ["entreprise", "secteur", "id_externe", "date_publication", "note", "titre", "texte"]

# Briques pour reconnaître les noms de personnes
MOT_NOM = r"[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ɏ\-]+"            # « Philippe », « GENTILLEAU », « Jean-Marc »
CIVILITE = r"(?:Monsieur|Madame|Mademoiselle|Mme|Mlle|Mr|Mrs|Ms|M\.)"
SALUTATION = r"(?:Bonjour|Bonsoir|Salut|Hello|Hi|Dear|Cher|Chère|Chers|Chères)"


def _nettoyer_texte(s):
    s = "" if pd.isna(s) else str(s)
    s = unicodedata.normalize("NFC", s)   # « é » écrit en 1 caractère, jamais « e » + accent
    s = s.replace("\u00a0", " ").replace("\u202f", " ")   # espaces insécables -> espace normal
    s = re.sub(r"\s+", " ", s)          # retours à la ligne et espaces multiples -> 1 espace
    return s.strip()


# ---------------------------------------------------------------------
#  Anonymisation : données personnelles -> balises
#  L'ordre compte : du motif le plus précis au plus général.
# ---------------------------------------------------------------------
_MOTS_APRES_CIVILITE = r"(?:Le|La|Les|Et|Ou|Votre|Vous|Nous|Merci|Bonjour|Monsieur|Madame|Mademoiselle)"
_MOTS_PAS_PRENOM = (r"(?:et|je|j|de|du|des|la|le|les|un|une|à|a|au|mais|donc|car|ou|pour|qui|que|"
                    r"suis|ai|est|client|cliente|depuis|avec|sur|il|elle|nous|vous|merci|bonjour|"
                    r"dans|en|par|ce|cette|mon|ma|mes|votre|and|from)")

_MOTIFS_ANONYMES = [
    # E-mail et lien
    (re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+"), "[EMAIL]"),
    (re.compile(r"\b(?:https?://|www\.)[^\s<>\"]+?(?=[.,;:!?)\]]*(?:\s|$))", re.I), "[URL]"),
    # Civilité + nom : « Monsieur Dupont », « Mme Martin Durand » -> « Monsieur [NOM] »
    (re.compile(rf"\b((?i:monsieur|madame|mademoiselle|mme|mlle|mr|m\.))[ ]+"
                rf"(?!{_MOTS_APRES_CIVILITE}\b){MOT_NOM}(?:[ ]+(?!{_MOTS_APRES_CIVILITE}\b){MOT_NOM})?"),
     r"\1 [NOM]"),
    # Présentation : « je m'appelle nathan zerbib » -> « je m'appelle [PRENOM] »
    (re.compile(rf"\b(je m['’]appelle|je me nomme|mon (?:pr[ée])?nom est)[ ]+[A-Za-zÀ-ɏ\-]+"
                rf"(?:[ ]+(?!{_MOTS_PAS_PRENOM}\b)[A-Za-zÀ-ɏ\-]+)?", re.I), r"\1 [PRENOM]"),
    # IBAN : 2 lettres, 2 chiffres, puis des blocs de 4 (FR76 3000 6000 0112 3456 7890 189)
    (re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){3,7}(?:[ ]?[A-Z0-9]{1,3})?\b", re.I), "[IBAN]"),
    # Suivi de colis : format postal international (LA123456789FR) et Colissimo (6A12345678901)
    (re.compile(r"\b[A-Z]{2}\d{9}[A-Z]{2}\b", re.I), "[SUIVI]"),
    (re.compile(r"\b\d[A-Z]\d{11}\b", re.I), "[SUIVI]"),
    # Mot-clé de suivi + code : « numéro de suivi : XY12345678 »
    (re.compile(r"(\b(?:suivi|colis|tracking)\s*(?:n[°ºo]\.?|num[ée]ro|#)?\s*:?\s*)"
                r"(?=[\w\-]*\d)[A-Z0-9][A-Z0-9\-]{5,}\b", re.I), r"\1[SUIVI]"),
    # Mot-clé de commande / facture / référence + identifiant (au moins 5 caractères dont un chiffre)
    (re.compile(r"(\b(?:commandes?|cde|cmd|factures?|r[ée]f[ée]rence|r[ée]f|dossier|ticket|contrat|"
                r"client|abonn[ée]|transaction)\s*(?:n[°ºo]\.?|num[ée]ro|num\.?|#)?\s*:?\s*)"
                r"(?=[\w\-]*\d)[A-Z0-9][A-Z0-9\-]{4,}\b", re.I), r"\1[NUM]"),
    # 16 chiffres groupés par 4 (numéro de commande ou de carte)
    (re.compile(r"(?<!\d)\d{4}(?:[ \-]\d{4}){3}(?!\d)"), "[NUM]"),
    # Téléphone français : 06 12 34 56 78, 06.12.34.56.78, +33 6 12 34 56 78, 0612345678
    (re.compile(r"(?<![\w+])(?:(?:\+|00)33[ .\-]?(?:\(0\))?[ .\-]?|0)[1-9](?:[ .\-]?\d{2}){4}(?!\d)"), "[TEL]"),
    # « n° » seul suivi d'un identifiant : « n° 12345L12345 »
    (re.compile(r"(\bn[°º]\s*:?\s*)(?=[\w\-]*\d)[A-Z0-9][A-Z0-9\-]{4,}\b", re.I), r"\1[NUM]"),
    # Reste : tout mot contenant 8 chiffres ou plus (collés ou mêlés de lettres)
    (re.compile(r"\b(?=(?:[A-Z\-]*\d){8})[A-Z0-9][A-Z0-9\-]*\b", re.I), "[NUM]"),
]


def _anonymiser_texte(s):
    """Remplace e-mails, liens, noms après civilité, téléphones, numéros de commande,
    codes de suivi, IBAN... par des balises.

    LIMITE ASSUMÉE : anonymisation « au mieux » par expressions régulières. Elle couvre les cas
    prévus (civilité + nom, « je m'appelle », formules d'appel et signatures des réponses) mais PAS
    un nom isolé au milieu d'une phrase (« j'ai parlé à Dupont »). Pour aller plus loin : un outil
    de reconnaissance d'entités nommées (spaCy, modèle français). À mentionner dans le rapport."""
    if not s:
        return s
    for motif, balise in _MOTIFS_ANONYMES:
        s = motif.sub(balise, s)
    return s


def _assembler(ti, tx):
    """Titre + texte, sans répéter le titre quand le texte le contient déjà."""
    if not ti or tx.startswith(ti):
        return tx
    return f"{ti} {tx}" if ti[-1] in ".!?:" else f"{ti}. {tx}"


# ---------------------------------------------------------------------
#  Réponses des entreprises : prénom du client en tête, signature de l'employé à la fin
# ---------------------------------------------------------------------
# Mots qui suivent « Bonjour » sans être un prénom (« Bonjour Nous sommes désolés... »)
_PAS_UN_PRENOM = (r"(?:Merci|Nous|Je|J|Suite|Toutes?|Tous|Tout|Il|Elle|On|Votre|Vos|Notre|Nos|Ravie?|"
                  r"Désolée?s?|Dommage|Encore|Après|Avant|Concernant|Pour|En|Et|Le|La|Les|L|Un|Une|"
                  r"Si|Dans|Comme|Malheureusement|Effectivement|Cet|Cette|Ce|C|Quel|Quelle|Bonne|Belle|"
                  r"Client|Cliente|Clients|Clientes|Madame|Monsieur|Mesdames|Messieurs|Mademoiselle|"
                  r"Thank|Thanks|Sorry|We|Customer|Team|Équipe|Equipe|À|A|Au)")

# Une formule d'appel peut être au début OU plus loin (réponses en plusieurs parties :
# « [Mise à jour : ... Damien] Bonjour Laurence, ... »). PRE = début de texte, ou après « ] \n . ! ? ».
# Groupe 1 = PRE, groupe 2 = formule (Bonjour...), groupe 3 = civilité (si présente).
PRE = r"(^|(?<=[\]\n.!?])[ ]*)"
# « Bonjour Monsieur Dupont, » -> « Bonjour Monsieur [NOM], »
_SALUT_CIVILITE = re.compile(
    rf"{PRE}({SALUTATION})[ ]+({CIVILITE})[ ]+(?!{CIVILITE}\b)[A-ZÀ-Þ][A-Za-zÀ-ɏ0-9\-'’.&() ]{{0,40}}?(?=[ ]*[,\n!?])")
# « Bonjour Philippe, » / « Chère Marie, » / « Bonjour Gilles Champalbert, » / « Bonjour Phil49, »
# / « Bonjour Catherine et tout d'abord merci » -> « Bonjour [PRENOM], »
_SALUT_PRENOM = re.compile(
    rf"{PRE}({SALUTATION})[ ]+(?!{_PAS_UN_PRENOM}\b)[A-ZÀ-Þ][A-Za-zÀ-ɏ0-9\-'’.&() ]{{0,40}}?(?=[ ]*[,\n!?]|[ ]+et\b)")
# Un seul mot juste avant la virgule est un nom, même « Le » ou « Elle » (« Bonjour Le, »),
# sauf les formules génériques (« Cher Client, », « Bonjour Madame, »)
_SALUT_MOT_SEUL = re.compile(
    rf"{PRE}({SALUTATION})[ ]+(?!(?:Clients?|Clientes?|Customers?|Madame|Monsieur|Mademoiselle|Mesdames|"
    rf"Messieurs|Tous|Toutes|Team|Équipe|Equipe|Ami|Amie|Amis)\b)[A-ZÀ-Þ][A-Za-zÀ-ɏ0-9\-]*(?=[ ]*,)")
# Prénom écrit en minuscules par le client (« Bonjour sylvie, »), sauf les mots génériques
_SALUT_MINUSCULE = re.compile(
    rf"{PRE}({SALUTATION})[ ]+(?!(?:clients?|clientes?|tous|toutes|merci|madame|monsieur|mesdames|messieurs|"
    rf"à|a|et|nous|vous|je|customer|team)\b)[a-zà-ɏ][a-zà-ɏ0-9\-]*(?=[ ]*,)")
# Signature d'employé au milieu du texte : « ... Bonne fin de journée, Damien] » ou « ..., Damien Bonjour Laurence »
_SIGNATURE_MILIEU = re.compile(rf"([,.]?\s+)({MOT_NOM})(?=\s*\]|\s+(?:Bonjour|Bonsoir)\b)")
# Mots capitalisés qui sont des noms d'entreprise et non des prénoms
_ORGANISATIONS = {"Boulanger", "Cdiscount", "Trustpilot", "Market", "Back", "Amazon", "Darty", "Fnac"}

# « Merci Michel pour votre retour » / « Merci, Caroline, pour ... » / « Merci Jean Yves pour ... »
# -> « Merci [PRENOM] pour ... ». Forme très fréquente (près de 3 % des réponses).
# Mots qui suivent « Merci » sans être un prénom : « Merci Beaucoup », « Merci Pour », « Merci Monsieur »...
_PAS_UN_PRENOM_MERCI = (r"(?:Beaucoup|Infiniment|Encore|Vivement|Bien|Mille|Pour|De|D|À|A|Du|Des|Au|Aux|En|Dans|"
                        r"Par|Avec|Sur|Si|Je|Il|Elle|Ça|Cela|Ceci|Vous|Nous|Votre|Vos|Ce|Cet|Cette|Un|Une|Les|"
                        r"Le|La|Et|Tout|Toute|Tous|Toutes|Monsieur|Madame|Mademoiselle|Mme|Mlle|Mr|Client|"
                        r"Cliente|Clients|Customer|Sincèrement|Cordialement)")
_MERCI_PRENOM = re.compile(
    rf"\b(Merci,?[ ]+)(?!{_PAS_UN_PRENOM_MERCI}\b)({MOT_NOM}(?:[ ]+{MOT_NOM})?)"
    rf"(?=,?[ ]+(?:pour|de|d['’]|à|du|des|ce|cet|cette|votre|vos|beaucoup|infiniment|encore)\b|[ ]*[,.!?]|$)")
# Signature anglaise : « Yours sincerely, Niyaz, Customer Advocacy Team. » -> « ..., [PRENOM], Customer... »
_SIGNATURE_ANGLAISE = re.compile(
    rf"\b((?:Yours sincerely|Yours faithfully|Sincerely|Best regards|Kind regards|Warm regards|Regards)[ ]*,?[ ]+)"
    rf"({MOT_NOM})(?=[ ]*[,.!]|[ ]*$)")
# « Christine - Equipe Rakuten France » -> « L'équipe Rakuten France »
_SIGNATURE_TIRET = re.compile(rf"\b({MOT_NOM})[ ]+[-–][ ]+(?:Equipe|Équipe)\b")

# Premiers mots d'une ligne de fin qui NE sont PAS un prénom (formules de politesse, etc.)
_FORMULES = {
    "cordialement", "sincèrement", "merci", "bien", "bonne", "belle", "excellente", "excellent",
    "très", "au", "à", "a", "nous", "votre", "vos", "service", "équipe", "equipe", "team",
    "customer", "kind", "best", "regards", "amicalement", "salutations", "respectueusement",
    "le", "la", "les", "un", "une", "je", "toute", "toutes", "tout", "tous", "en", "dans", "pour",
    "avec", "encore", "bonjour", "bonsoir", "madame", "monsieur", "mademoiselle", "mme", "mlle",
    "client", "cliente", "clients", "cher", "chère", "ravi", "ravie", "désolé", "désolée",
    "navrés", "navré", "navrée", "désolés", "désolées", "dommage", "bravo", "félicitations", "bienvenue",
    "courage", "parfait", "super", "malheureusement", "effectivement", "exactement", "pardon",
}
# Ligne « d'équipe » sous la signature : « ABDOU \n Équipe de service à la clientèle »
_LIGNE_EQUIPE = re.compile(r"^(?:Équipe|Equipe|Service|Team|Customer|L['’][ée]quipe)\b", re.I)


def _est_prenom(mot):
    return mot.lower() not in _FORMULES


def _signature(ligne):
    """Remplace par « L'équipe » une signature d'employé ; renvoie la ligne inchangée sinon."""
    ligne = ligne.strip()
    # « Léa, Promovacances » / « Nelson de Back Market » / « Julie de l'équipe Cdiscount »
    # / « Violaine, de l'équipe Boulanger. » (virgule PUIS « de »)
    m = re.fullmatch(rf"({MOT_NOM})(?:[ ]*[,\-–][ ]*(?:de[ ]+)?|[ ]+de[ ]+)((?:[A-ZÀ-Þ]|l['’][ée]quipe).{{0,60}})", ligne)
    if m and _est_prenom(m.group(1)):
        return "L'équipe"
    # « A bientôt, Violaine, de l'équipe Boulanger. » -> « A bientôt, L'équipe »
    m = re.fullmatch(rf"(.*,[ ]*)({MOT_NOM})(?:[ ]+{MOT_NOM})?,?[ ]+de[ ]+l['’][ée]quipe[ ]+[^,]{{1,40}}", ligne)
    if m and _est_prenom(m.group(2)):
        return m.group(1) + "L'équipe"
    # « Charlotte. » : prénom seul suivi d'un point (les mots courants comme « Navrés. » sont dans _FORMULES)
    m = re.fullmatch(rf"({MOT_NOM})\.", ligne)
    if m and _est_prenom(m.group(1)):
        return "L'équipe"
    # « Damien » / « ABDOU » / « Emma » seuls sur la ligne
    m = re.fullmatch(rf"({MOT_NOM})(?:[ ]+{MOT_NOM})?", ligne)
    if m and _est_prenom(m.group(1)):
        return "L'équipe"
    # « Cordialement, Siya » -> « Cordialement, L'équipe »
    m = re.fullmatch(rf"(.*,[ ]*)({MOT_NOM})(?:[ ]+{MOT_NOM})?[ ]*[.!]?", ligne)
    if m and _est_prenom(m.group(2)):
        return m.group(1) + "L'équipe"
    return ligne


def _anonymiser_reponse(s):
    """Réponse de l'entreprise -> texte sans prénom/nom du client ni signature d'employé,
    avec le même masquage que les avis clients, puis nettoyé. NULL reste NULL (None)."""
    if s is None or pd.isna(s):
        return None
    s = unicodedata.normalize("NFC", str(s)).replace("\r", "").replace("\t", " ")
    s = s.replace(" ", " ").replace(" ", " ").strip()   # espaces insécables

    # 1) Formule d'appel. Les mots qui ne sont pas des noms restent (« Bonjour, merci... »).
    s = _SALUT_CIVILITE.sub(r"\1\2 \3 [NOM]", s)
    s = _SALUT_PRENOM.sub(r"\1\2 [PRENOM]", s)
    s = _SALUT_MOT_SEUL.sub(r"\1\2 [PRENOM]", s)
    s = _SALUT_MINUSCULE.sub(r"\1\2 [PRENOM]", s)
    s = _MERCI_PRENOM.sub(lambda m: m.group(1) + (m.group(2) if m.group(2).split()[0] in _ORGANISATIONS else "[PRENOM]"), s)
    s = _SIGNATURE_ANGLAISE.sub(lambda m: m.group(1) + ("[PRENOM]" if m.group(2) not in _ORGANISATIONS else m.group(2)), s)
    s = _SIGNATURE_MILIEU.sub(
        lambda m: m.group(1) + ("L'équipe" if _est_prenom(m.group(2)) and m.group(2) not in _ORGANISATIONS else m.group(2)), s)
    # Prénom seul sur sa ligne, juste après une ligne qui finit par une virgule (« À tout de suite, » / « Damien »)
    lignes_s = s.split("\n")
    for i in range(1, len(lignes_s)):
        mot = lignes_s[i].strip()
        if lignes_s[i - 1].rstrip().endswith(",") and re.fullmatch(MOT_NOM, mot) and _est_prenom(mot) \
                and mot not in _ORGANISATIONS:
            lignes_s[i] = "L'équipe"
    s = "\n".join(lignes_s)

    # 2) Signature : dernière phrase de la dernière ligne non vide
    lignes = [l for l in s.split("\n") if l.strip()]
    if lignes:
        phrases = re.split(r"(?<=[.!?…])[ ]+", lignes[-1].strip())
        phrases[-1] = _signature(phrases[-1])
        lignes[-1] = " ".join(phrases)
        # ... et la ligne au-dessus si la dernière est « Équipe de service à la clientèle »
        if len(lignes) >= 2 and _LIGNE_EQUIPE.match(lignes[-1]):
            lignes[-2] = _signature(lignes[-2])
    s = "\n".join(lignes)

    # « Sabrina de l'équipe Boulanger » (ou « Sabrina l'équipe ») resté au MILIEU du texte -> « L'équipe ».
    # Après la signature de fin, qui retire aussi le nom de l'entreprise (« Julie de l'équipe Cdiscount »).
    s = re.sub(rf"\b({MOT_NOM})(?:[ ]+{MOT_NOM})?,?[ ]+(?:de[ ]+)?(l['’]?[ée]quipe)\b",
               lambda m: "L'équipe" if _est_prenom(m.group(1)) and m.group(1) not in _ORGANISATIONS else m.group(0), s)
    s = _SIGNATURE_TIRET.sub(lambda m: "L'équipe" if _est_prenom(m.group(1)) and m.group(1) not in _ORGANISATIONS else m.group(0), s)

    # 3) Même masquage que les avis clients ([TEL], [NUM], [EMAIL], [URL], [IBAN]...)
    s = _anonymiser_texte(s)
    return _nettoyer_texte(s)


def nettoyer(df, source="trustpilot", garder_reponses=False, verbose=True):
    """Prend le tableau brut, renvoie le tableau nettoyé."""
    df = df.copy()
    n0 = len(df)
    manquantes = [c for c in OBLIGATOIRES if c not in df.columns]
    assert not manquantes, f"Colonnes absentes du brut : {manquantes}"

    for c in ["date_publication", "date_collecte", "date_experience", "reponse_date"]:
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], utc=True, errors="coerce")
    df["note"] = pd.to_numeric(df["note"], errors="coerce")

    df = df.dropna(subset=["id_externe", "date_publication", "note"])
    df = df[df["note"].between(1, 5)]
    n_invalides = n0 - len(df)
    df = df.drop_duplicates(subset=["id_externe"])       # doublons sur l'identifiant, pas sur le texte
    n_doublons = n0 - n_invalides - len(df)

    df["titre_clean"] = df["titre"].map(_nettoyer_texte).str.rstrip("…").str.strip().map(_anonymiser_texte)
    df["texte_clean"] = df["texte"].map(_nettoyer_texte).map(_anonymiser_texte)
    df["texte_total"] = [_assembler(a, b) for a, b in zip(df["titre_clean"], df["texte_clean"])]
    df["titre_tronque"] = df["titre"].fillna("").str.endswith("…")

    df["longueur_texte"] = df["texte_clean"].str.len()
    df["texte_court"] = df["longueur_texte"] < 15
    df["tout_majuscules"] = (df["longueur_texte"] > 20) & (df["texte_clean"] == df["texte_clean"].str.upper())
    df["a_emoji"] = df["texte_clean"].str.contains(r"[\U0001F300-\U0001FAFF☀-➿]", regex=True)

    if "reponse_texte" in df.columns:
        df["a_reponse"] = df["reponse_texte"].notna()
        if "reponse_date" in df.columns:
            h = (df["reponse_date"] - df["date_publication"]).dt.total_seconds() / 3600
            df["delai_reponse_heures"] = h.where(h >= 0)
        if garder_reponses:
            df["reponse_anonymisee"] = df["reponse_texte"].map(_anonymiser_reponse)

    if "date_experience" in df.columns:
        df["delai_pub_experience_jours"] = (df["date_publication"].dt.normalize() - df["date_experience"].dt.normalize()).dt.days
    df["sentiment_etoiles"] = pd.cut(df["note"], [0, 2, 3, 5], labels=["negatif", "neutre", "positif"]).astype(str)
    df["source"] = source

    colonnes = ["source", "entreprise", "secteur", "id_externe", "date_publication", "date_collecte",
                "date_experience", "delai_pub_experience_jours", "note", "sentiment_etoiles", "verifie",
                "auteur_nb_avis", "titre_clean", "titre_tronque", "texte_clean", "texte_total",
                "longueur_texte", "texte_court", "tout_majuscules", "a_emoji", "a_reponse", "delai_reponse_heures"]
    if garder_reponses:
        colonnes.append("reponse_anonymisee")
    df = df[[c for c in colonnes if c in df.columns]].sort_values("date_publication", ascending=False)

    if verbose:
        print(f"Nettoyage : {n0} lignes en entrée, {n_invalides} invalides, {n_doublons} doublons, {len(df)} en sortie")
        print(f"Drapeaux : textes courts={df.texte_court.sum()}, majuscules={df.tout_majuscules.sum()}, "
              f"emoji={df.a_emoji.sum()}, titres tronqués={df.titre_tronque.sum()}")
        print("Répartition entreprise x note (vérifier l'équilibre) :")
        print(pd.crosstab(df["entreprise"], df["note"], margins=True).to_string())
    return df


# =====================================================================
#  Version « base de données » : table avis  ->  table avis_traites
# =====================================================================
MIN_MOTS = 3          # en dessous, pas de détection de langue (langdetect peu fiable)
                      # l'avis reste gardé : « pas mal » + la note sert à l'entraînement
MIN_CAR_DOUBLON = 80  # on ne cherche des doublons que sur des textes assez longs
                      # (« Livraison rapide » écrit par deux clients n'est pas un doublon)


# Date des règles de nettoyage : À CHANGER à chaque modification des règles, pour savoir
# avec quelle version chaque ligne de avis_traites (et donc chaque modèle) a été produite.
VERSION_NETTOYAGE = "2026-11-18.3"   # .2 : a_garder = qualité seule + a_garder_nlp ; .3 : « Merci Prénom », signatures anglaises/tiret

# Répartition FIXE des avis entre entraînement / validation / test (en %).
# Calculée à partir de avis_id : un avis reste dans le même jeu à chaque relance.
PART_TRAIN, PART_VALIDATION = 80, 10      # le reste (10 %) = test


def _jeu(avis_id):
    """train / validation / test, toujours le même pour un avis_id donné."""
    rang = int(hashlib.md5(str(avis_id).encode()).hexdigest()[:8], 16) % 100
    if rang < PART_TRAIN:
        return "train"
    return "validation" if rang < PART_TRAIN + PART_VALIDATION else "test"


def _sentiment(note):
    """Étiquette dérivée des étoiles : 1-2 negatif, 3 neutre, 4-5 positif."""
    if pd.isna(note):
        return None
    return "negatif" if note <= 2 else ("neutre" if note == 3 else "positif")


CAR_DETECTION = 500  # langdetect n'a pas besoin du texte entier pour reconnaître la langue
TAILLE_LOT_LANGUE = 2000

# Langues possibles et probabilité de départ. Les avis viennent de sites français : sans ce
# coup de pouce, langdetect classait des avis courts ou en MAJUSCULES en catalan, allemand,
# espagnol... (« Très bien merci » -> es, « SIMPLE ET RAPIDE » -> en). Langue absente = exclue.
LANGUES_PRIOR = {"fr": 0.80, "en": 0.10, "es": 0.02, "it": 0.02, "de": 0.02, "pt": 0.02,
                 "nl": 0.02, "ru": 0.01, "ar": 0.01}
# Une autre langue que le français n'est retenue que si langdetect en est sûr ET que l'avis
# est assez long ; sinon on garde « fr ».
PROBA_MIN_ETRANGER = 0.90
MOTS_MIN_ETRANGER = 5

_factory = None


def _init_langdetect():
    """Profils de langues chargés UNE fois par processus (et non à chaque avis)."""
    global _factory
    try:
        from langdetect import DetectorFactory
        from langdetect import detector_factory
        DetectorFactory.seed = 0           # résultat reproductible
        detector_factory.init_factory()
        _factory = detector_factory._factory
    except ImportError:
        _factory = None


def _langue(texte, nb_mots):
    """Code langue (fr, en...) ou None si le texte est trop court pour être fiable."""
    if nb_mots < MIN_MOTS or _factory is None:
        return None
    try:
        d = _factory.create()
        d.set_prior_map(LANGUES_PRIOR)
        d.append(texte[:CAR_DETECTION].lower())   # les MAJUSCULES trompent langdetect
        meilleure = d.get_probabilities()[0]
    except Exception:                      # texte sans lettres, etc.
        return None
    if meilleure.lang != "fr" and (meilleure.prob < PROBA_MIN_ETRANGER or nb_mots < MOTS_MIN_ETRANGER):
        return "fr"
    return meilleure.lang


def _langues_lot(lot):
    return [_langue(t, n) for t, n in lot]


def detecter_langues(textes, nb_mots, verbose=True):
    """Détection de langue en parallèle (un processus par cœur), avec progression."""
    paires = list(zip(textes, nb_mots))
    if len(paires) <= TAILLE_LOT_LANGUE:
        _init_langdetect()
        return _langues_lot(paires)
    from concurrent.futures import ProcessPoolExecutor
    lots = [paires[i:i + TAILLE_LOT_LANGUE] for i in range(0, len(paires), TAILLE_LOT_LANGUE)]
    res, fait = [], 0
    with ProcessPoolExecutor(initializer=_init_langdetect) as ex:
        for r in ex.map(_langues_lot, lots):
            res.extend(r)
            fait += len(r)
            if verbose:
                print(f"  langues : {fait}/{len(paires)} avis", flush=True)
    return res


def traiter_avis(df, ids_a_traiter=None, verbose=True):
    """Entrée : lignes de la table `avis` (id, entreprise_id, note, titre, texte, date_publication,
    reponse_texte).
    Sortie : lignes prêtes pour la table `avis_traites`.
    ids_a_traiter : si donné, seuls ces avis sont renvoyés (les doublons sont quand même
    cherchés sur TOUT `df`, pour comparer les nouveaux avis aux anciens)."""
    df = df.copy()
    # Anonymisation AVANT doublons, langue et nb_mots : on ne garde aucune donnée personnelle.
    titre = df["titre"].map(_nettoyer_texte).str.rstrip("…").str.strip().map(_anonymiser_texte)
    texte = df["texte"].map(_nettoyer_texte).map(_anonymiser_texte)

    # Doublons : même entreprise + même texte (en minuscules, sans ponctuation). On garde le plus ancien.
    cle = texte.str.lower().str.replace(r"[^\w]+", " ", regex=True).str.strip()
    cand = pd.DataFrame({"e": df["entreprise_id"], "c": cle, "d": df["date_publication"], "id": df["id"]})
    cand = cand[cand["c"].str.len() >= MIN_CAR_DOUBLON].sort_values(["d", "id"])
    doublon = pd.Series(False, index=df.index)
    doublon.loc[cand.index[cand.duplicated(subset=["e", "c"], keep="first")]] = True

    garder = df["id"].isin(ids_a_traiter) if ids_a_traiter is not None else pd.Series(True, index=df.index)
    out = pd.DataFrame({
        "avis_id": df["id"].astype("int64"),
        "titre_propre": titre,
        "texte_propre": texte,
        "est_doublon": doublon,
    }, index=df.index)[garder]
    out["nb_mots"] = out["texte_propre"].str.split().str.len().fillna(0).astype(int)
    out["langue"] = detecter_langues(out["texte_propre"], out["nb_mots"], verbose=verbose)

    # Titre + texte en un seul champ (le titre n'est pas répété si le texte commence par lui,
    # ce qui arrive avec les titres tronqués « … »)
    out["texte_complet"] = [_assembler(a, b) for a, b in zip(out["titre_propre"], out["texte_propre"])]
    out["titre_tronque"] = df.loc[garder, "titre"].fillna("").astype(str).str.rstrip().str.endswith("…")
    out["texte_court"] = out["nb_mots"] < MIN_MOTS
    out["tout_majuscules"] = (out["texte_propre"].str.len() > 20) & (out["texte_propre"] == out["texte_propre"].str.upper())

    # Réponse de l'entreprise, anonymisée (prénom du client, signature de l'employé, numéros...).
    # Pas de réponse -> NULL (et non une chaîne vide).
    if "reponse_texte" in df.columns:
        out["reponse_propre"] = df.loc[garder, "reponse_texte"].map(_anonymiser_reponse).astype(object)
    else:
        out["reponse_propre"] = None

    # Pour l'entraînement : entreprise, étiquette (note -> sentiment), jeu fixe, version des règles
    out["entreprise_id"] = df.loc[garder, "entreprise_id"].astype("int64")
    if "note" in df.columns:
        out["note"] = pd.to_numeric(df.loc[garder, "note"], errors="coerce").astype("Int64")
    else:
        out["note"] = pd.NA
    out["sentiment"] = out["note"].map(_sentiment).astype(object)
    out["jeu"] = out["avis_id"].map(_jeu)
    out["version_nettoyage"] = VERSION_NETTOYAGE

    out = out[["avis_id", "entreprise_id", "titre_propre", "texte_propre", "texte_complet", "nb_mots",
               "langue", "est_doublon", "titre_tronque", "texte_court", "tout_majuscules", "reponse_propre",
               "note", "sentiment", "jeu", "version_nettoyage"]]

    # Deux colonnes, deux usages :
    #  - a_garder     = QUALITÉ seule (ni doublon, ni texte vide). Sert aux indicateurs du tableau de bord :
    #                   les avis écrits dans une autre langue comptent aussi dans la note d'une entreprise.
    #  - a_garder_nlp = a_garder ET français (ou langue vide, texte trop court pour détecter : les sites
    #                   sources sont français). Sert à l'entraînement du modèle de langue.
    francais = out["langue"].isna() | (out["langue"] == "fr")
    out["a_garder"] = (~out["est_doublon"]) & (out["nb_mots"] >= 1)
    out["a_garder_nlp"] = out["a_garder"] & francais
    out["traite_le"] = pd.Timestamp.now(tz="UTC")

    if verbose:
        print(f"Traitement : {len(out)} avis | doublons : {int(out.est_doublon.sum())} | "
              f"trop courts (<{MIN_MOTS} mots) : {int((out.nb_mots < MIN_MOTS).sum())} | "
              f"autre langue : {int((~francais).sum())} | "
              f"a_garder : {int(out.a_garder.sum())} | a_garder_nlp : {int(out.a_garder_nlp.sum())} | "
              f"réponses : {int(out.reponse_propre.notna().sum())}")
        print("Langues détectées :", out["langue"].value_counts(dropna=False).head(5).to_dict())
        gardes = out[out["a_garder_nlp"]]
        print("Avis gardés par sentiment et par jeu (vérifier l'équilibre) :")
        print(pd.crosstab(gardes["sentiment"], gardes["jeu"], margins=True).to_string())
    return out


if __name__ == "__main__":
    brut = pd.read_csv(sys.argv[1], encoding="utf-8-sig")
    nettoyer(brut).to_csv(sys.argv[2], index=False, encoding="utf-8-sig")