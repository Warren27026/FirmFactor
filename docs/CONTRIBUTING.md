# Règles de travail en équipe

## Git
1. On ne travaille JAMAIS directement sur `main`.
2. Une branche par tâche : `feature/scraper-trustpilot`, `fix/api-health`, `docs/etat-de-l-art`
3. Petits commits clairs : `feat: ajoute le scraper Avis-Clients`
4. Pull Request → relue et validée par un autre membre → merge.

```bash
git checkout main && git pull            # se mettre à jour
git checkout -b feature/ma-tache         # créer sa branche
git add . && git commit -m "feat: ..."   # enregistrer
git push -u origin feature/ma-tache      # envoyer, puis ouvrir la PR sur GitHub
```

## Secrets et données
- Les clés et mots de passe vont UNIQUEMENT dans `.env` (jamais sur GitHub).
- Les données collectées vont dans Supabase, pas dans le dépôt.

## Rituels
- Début de séance (15 min) : fait / à faire / bloquants.
- Fin de séance (30 min) : mise à jour du tableau GitHub Projects + journal de bord (`docs/journal.md`).
