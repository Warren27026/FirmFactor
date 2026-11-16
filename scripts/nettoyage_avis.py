"""Nettoyage des avis (FirmFactor) - fonction réutilisable.

Deux usages :
  1) Fichier :   python nettoyage_avis.py data/raw/avis.csv data/clean/avis_nettoyes.csv
  2) Importée par pipeline_supabase.py : from nettoyage_avis import nettoyer

Le brut n'est jamais modifié : on produit un nouveau tableau.
"""
import re
import sys

import pandas as pd

OBLIGATOIRES = ["entreprise", "secteur", "id_externe", "date_publication", "note", "titre", "texte"]
NOM = r"[A-ZÉÈ][\w\-'’\.]*"


def _nettoyer_texte(s):
    s = "" if pd.isna(s) else str(s)
    s = s.replace(" ", " ")
    s = re.sub(r"\s+", " ", s)          # retours à la ligne et espaces multiples -> 1 espace
    return s.strip()


def _assembler(ti, tx):
    """Titre + texte, sans répéter le titre quand le texte le contient déjà."""
    if not ti or tx.startswith(ti):
        return tx
    return f"{ti} {tx}" if ti[-1] in ".!?:" else f"{ti}. {tx}"


def _anonymiser_reponse(s):
    if pd.isna(s):
        return pd.NA
    s = str(s).replace("\r", "").strip()
    s = re.sub(r"^(Bonjour|Bonsoir|Salut|Hello)\s+(?!(?:Merci|Nous|Je|Suite|Toutes?|Tout|Il|Votre|Ravi|Désolé|Dommage|Encore)\b)[^,\n]{1,45}(?=,|\n)", r"\1", s)
    s = re.sub(rf"^(Bonjour|Bonsoir|Salut|Hello)\s+{NOM}(?:\s+{NOM}){{0,2}}\s*(?=,|\n| et |\.)", r"\1", s)
    s = re.sub(rf"{NOM},?\s+de l['’]équipe\s+\S+\s*$", "L'équipe", s)
    lignes = s.rstrip().split("\n")
    if len(lignes) >= 2 and re.fullmatch(rf"{NOM}(?: {NOM})?", lignes[-1].strip()):
        lignes = lignes[:-1]
    s = _nettoyer_texte(" ".join(lignes))
    return re.sub(rf"([!,.])\s+{NOM}$", r"\1", s)


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

    df["titre_clean"] = df["titre"].map(_nettoyer_texte).str.rstrip("…").str.strip()
    df["texte_clean"] = df["texte"].map(_nettoyer_texte)
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
MIN_CAR_DOUBLON = 30  # on ne cherche des doublons que sur des textes assez longs
                      # (« Livraison rapide » écrit par deux clients n'est pas un doublon)


CAR_DETECTION = 500   # langdetect n'a pas besoin du texte entier pour reconnaître la langue
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
    """Entrée : lignes de la table `avis` (id, entreprise_id, titre, texte, date_publication).
    Sortie : lignes prêtes pour la table `avis_traites`.
    ids_a_traiter : si donné, seuls ces avis sont renvoyés (les doublons sont quand même
    cherchés sur TOUT `df`, pour comparer les nouveaux avis aux anciens)."""
    df = df.copy()
    titre = df["titre"].map(_nettoyer_texte).str.rstrip("…").str.strip()
    texte = df["texte"].map(_nettoyer_texte)

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
    out = out[["avis_id", "titre_propre", "texte_propre", "nb_mots", "langue", "est_doublon"]]

    out["a_garder"] = (~out["est_doublon"]) & (out["nb_mots"] >= 1)   # on écarte doublons et textes vides
    out["traite_le"] = pd.Timestamp.now(tz="UTC")

    if verbose:
        print(f"Traitement : {len(out)} avis | doublons : {int(out.est_doublon.sum())} | "
              f"trop courts (<{MIN_MOTS} mots) : {int((out.nb_mots < MIN_MOTS).sum())} | "
              f"a_garder : {int(out.a_garder.sum())}")
        print("Langues détectées :", out["langue"].value_counts(dropna=False).head(5).to_dict())
    return out


if __name__ == "__main__":
    brut = pd.read_csv(sys.argv[1], encoding="utf-8-sig")
    nettoyer(brut).to_csv(sys.argv[2], index=False, encoding="utf-8-sig")