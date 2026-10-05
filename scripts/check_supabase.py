"""Teste la connexion à la base Supabase. Lancer : python scripts/check_supabase.py"""
import os

import psycopg
from dotenv import load_dotenv

load_dotenv()
url = os.getenv("DATABASE_URL")

if not url or "XXXXXXXX" in url:
    raise SystemExit("❌ DATABASE_URL manquant : remplis ton fichier .env")

with psycopg.connect(url) as conn:
    version = conn.execute("select version()").fetchone()[0]
    print("✅ Connexion Supabase OK :", version.split(",")[0])
