# Stockage Trustpilot selon le mapping FirmFactor

Depuis la racine du projet :

```powershell
python -m scripts.scrape_trustpilot_massif --quota 500 --max-pages 30
python -m scripts.importer_pages --dry-run
python -m scripts.importer_pages
```

Le premier script conserve chaque page dans `data/pages/<domaine>/pageNNNN.html` et
maintient son checkpoint dans `data/massif/`. Un domaine contenant `/` est encodé
dans le nom du dossier (`/` devient `%2F`). Le second lit `__NEXT_DATA__` de ces
pages, applique `extraire_avis()` et insère les données dans Supabase. `--dry-run`
valide les pages sans connexion ni écriture en base.

Pour préparer une base neuve, examiner puis appliquer `sql/schema_firmfactor.sql`
dans le SQL Editor Supabase. Si les tables existent déjà, vérifier leurs colonnes et
contraintes avant toute modification. Placer la chaîne de connexion PostgreSQL
`DATABASE_URL` dans `.env` (voir `.env.example`) ; ne jamais la committer.

Les secteurs `commerce`, `voyage` et `livraison` du CSV source deviennent
respectivement `commerce_en_ligne`, `voyages` et `livraison_colis`. Une date sans
fuseau ou une note hors de 1 à 5 fait rejeter la page concernée. Une valeur absente
devient `NULL`. Les noms, profils et autres identifiants d'auteur présents dans les
pages brutes ne sont jamais envoyés vers les tables `entreprises` ou `avis`.
`auteur_nb_avis` est le seul champ conservé concernant l'auteur.

La contrainte `(entreprise_id, id_externe)` évite les doublons entre pages et
réimports. Les pages brutes restent locales pour permettre un nouvel import après
une modification du mapping. Le CSV historique `data/massif/avis_massifs.csv` sert
encore de checkpoint au collecteur ; il n'est pas la source de l'import
Supabase et sa colonne `auteur` a été retirée. Les colonnes `date_experience`,
`verifie`, `reponse_texte`, `reponse_date` et `auteur_nb_avis` peuvent être
complétées depuis les pages HTML avec `python -m scripts.enrichir_avis_massifs`.
Une valeur absente reste vide. Ne pas pousser ce CSV sur Git.
