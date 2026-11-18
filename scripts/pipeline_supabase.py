"""Pipeline FirmFactor : Supabase (table avis) -> nettoyage -> Supabase (table avis_traites).

Usage (depuis la racine du projet, environnement virtuel activé) :
    python scripts/pipeline_supabase.py            # SIMULATION : lit, traite, n'écrit rien
    python scripts/pipeline_supabase.py --ecrire   # écrit dans avis_traites
    python scripts/pipeline_supabase.py --tout     # retraite aussi les avis déjà présents dans avis_traites

Par défaut, seuls les avis absents de avis_traites sont traités (les relances sont rapides).

Idempotent : on peut le relancer autant de fois qu'on veut, un avis déjà traité est mis à jour,
jamais dupliqué (clé = avis_id). Le secret (DATABASE_URL) vient du fichier .env, jamais du code.
"""
import argparse
import os
import sys

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import MetaData, Table, create_engine, inspect, text
from sqlalchemy.dialects import postgresql, sqlite

from nettoyage_avis import traiter_avis

TABLE_BRUTE = "avis"
TABLE_TRAITEE = "avis_traites"
CLE = "avis_id"
TAILLE_LOT = 1000


def connexion():
    load_dotenv()
    url = os.getenv("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL introuvable : vérifiez votre fichier .env (voir .env.example).")
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):      # pilote psycopg 3, déjà présent dans requirements.txt
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    engine = create_engine(url, pool_pre_ping=True)
    try:                                     # test rapide, avec un message clair si ça échoue
        with engine.connect():
            pass
    except Exception as e:
        premiere_ligne = str(e).splitlines()[0] if str(e) else type(e).__name__
        sys.exit(
            "Connexion à la base impossible.\n"
            f"Détail : {premiere_ligne}\n"
            "Pistes : 1) mot de passe incorrect ou périmé dans .env ; "
            "2) caractères spéciaux du mot de passe non encodés (@ # / : ? % -> %40 %23 %2F %3A %3F %25) ; "
            "3) utiliser la chaîne « Session pooler » (Connect dans Supabase) ; "
            "4) projet en pause (le relancer dans Supabase)."
        )
    return engine


def lire_brut(engine):
    sql = f"SELECT id, entreprise_id, note, titre, texte, date_publication, reponse_texte FROM {TABLE_BRUTE}"
    return pd.read_sql(text(sql), engine)


def ids_deja_traites(engine):
    return set(pd.read_sql(text(f"SELECT {CLE} FROM {TABLE_TRAITEE}"), engine)[CLE])


def verifier_table(engine, df):
    cibles = {c["name"] for c in inspect(engine).get_columns(TABLE_TRAITEE)}
    manque = [c for c in df.columns if c not in cibles]
    if manque:
        sys.exit(f"La table {TABLE_TRAITEE} n'a pas les colonnes : {manque}")


def vers_python(df):
    """NaN / NaT / pd.NA -> None (vrais NULL) ; numpy -> types Python."""
    df = df.astype(object).where(df.notna(), None)
    return df.to_dict(orient="records")


def ecrire(engine, df):
    table = Table(TABLE_TRAITEE, MetaData(), autoload_with=engine)
    insert = (postgresql if engine.dialect.name == "postgresql" else sqlite).insert
    modifiables = [c for c in df.columns if c != CLE]
    lignes = vers_python(df)
    with engine.begin() as conn:     # une transaction : tout passe ou rien ne passe
        for i in range(0, len(lignes), TAILLE_LOT):
            lot = lignes[i:i + TAILLE_LOT]
            req = insert(table).values(lot)
            req = req.on_conflict_do_update(index_elements=[CLE], set_={c: req.excluded[c] for c in modifiables})
            conn.execute(req)
            print(f"  {min(i + TAILLE_LOT, len(lignes))}/{len(lignes)} lignes écrites", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ecrire", action="store_true", help="écrire dans la table (sinon simulation)")
    ap.add_argument("--tout", action="store_true", help="retraiter tous les avis, même ceux déjà dans avis_traites")
    args = ap.parse_args()

    engine = connexion()
    brut = lire_brut(engine)
    print(f"{len(brut)} avis lus dans {TABLE_BRUTE}", flush=True)
    if brut.empty:
        return
    if args.tout:
        a_traiter = None
    else:
        deja = ids_deja_traites(engine)
        a_traiter = set(brut["id"]) - deja
        print(f"{len(deja)} déjà traités, {len(a_traiter)} nouveaux à traiter (--tout pour tout refaire)", flush=True)
        if not a_traiter:
            print("Rien à faire.")
            return
    traite = traiter_avis(brut, ids_a_traiter=a_traiter)
    verifier_table(engine, traite)
    if not args.ecrire:
        print("\nSIMULATION : rien n'a été écrit. Relancez avec --ecrire pour enregistrer.")
        print(traite.head(3).to_string(max_colwidth=45))
        return
    ecrire(engine, traite)
    n = pd.read_sql(text(f"SELECT COUNT(*) AS n FROM {TABLE_TRAITEE}"), engine)["n"][0]
    print(f"Terminé. {TABLE_TRAITEE} contient maintenant {n} lignes.")


if __name__ == "__main__":
    main()