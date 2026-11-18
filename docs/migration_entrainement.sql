-- À lancer UNE FOIS dans Supabase (SQL Editor). N'efface aucune donnée.
-- Prépare avis_traites pour l'entraînement des modèles : étiquette, jeu fixe, version.
-- Les colonnes sont remplies par scripts/pipeline_supabase.py --tout --ecrire.

alter table avis_traites
  add column if not exists entreprise_id     bigint references entreprises(id),
  add column if not exists note              smallint check (note between 1 and 5),
  -- étiquette dérivée des étoiles : 1-2 = negatif, 3 = neutre, 4-5 = positif
  add column if not exists sentiment         text check (sentiment in ('negatif', 'neutre', 'positif')),
  -- jeu FIXE calculé à partir de avis_id (80 / 10 / 10) : un avis ne change jamais de jeu
  add column if not exists jeu               text check (jeu in ('train', 'validation', 'test')),
  -- date des règles de nettoyage qui ont produit la ligne (VERSION_NETTOYAGE dans nettoyage_avis.py)
  add column if not exists version_nettoyage text;

-- a_garder     = qualité seule (indicateurs du tableau de bord, toutes langues)
-- a_garder_nlp = a_garder ET français (ou langue vide) : c'est le filtre de l'entraînement
alter table avis_traites add column if not exists a_garder_nlp boolean;

-- Vue unique pour l'entraînement : uniquement les avis gardés pour le NLP, prêts à l'emploi.
-- reponse_propre n'y figure PAS : le ton de la réponse de l'entreprise trahit la note.
create or replace view v_entrainement with (security_invoker = true) as
select
  t.avis_id,
  t.texte_complet      as texte,         -- titre + texte, nettoyé et anonymisé
  t.note,
  t.sentiment,
  t.jeu,
  t.entreprise_id,
  e.nom                as entreprise,
  e.secteur,
  a.date_publication,
  t.nb_mots,
  t.texte_court,
  t.version_nettoyage
from avis_traites t
join avis a        on a.id = t.avis_id
join entreprises e on e.id = t.entreprise_id
where t.a_garder_nlp;
