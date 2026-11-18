-- =====================================================================
-- FirmFactor — vues d'analyse (à lancer dans le SQL Editor de Supabase)
-- Non destructif pour les données : ne modifie aucune table.
--   avis_analyse : un avis par ligne, prêt pour les indicateurs
--   avis_hebdo   : indicateurs par entreprise et par semaine (remplace l'ancienne vue)
-- =====================================================================

-- 1. AVIS_ANALYSE : source unique pour les indicateurs (avis gardés uniquement)
create or replace view avis_analyse with (security_invoker = true) as
select
  a.id                                                        as avis_id,
  a.entreprise_id,
  e.nom,
  e.secteur,
  a.note,
  t.sentiment,                                                -- negatif / neutre / positif
  a.verifie,
  (a.date_publication at time zone 'Europe/Paris')::date      as jour,       -- heure de Paris
  date_trunc('week',  a.date_publication at time zone 'Europe/Paris')::date as semaine,
  date_trunc('month', a.date_publication at time zone 'Europe/Paris')::date as mois,
  a.reponse_texte is not null                                 as a_reponse,
  case when a.reponse_date >= a.date_publication
       then round(extract(epoch from a.reponse_date - a.date_publication) / 3600.0, 1)
  end                                                         as delai_reponse_heures,
  (a.date_publication at time zone 'Europe/Paris')::date - a.date_experience
                                                              as delai_experience_jours,
  -- false = période où la collecte était plafonnée (note moyenne faussée)
  (a.date_publication at time zone 'Europe/Paris')::date
      >= coalesce(e.debut_couverture, date '1900-01-01')      as dans_couverture,
  t.texte_complet,
  t.reponse_propre,
  t.nb_mots,
  t.jeu
from avis a
join avis_traites t on t.avis_id = a.id
join entreprises  e on e.id = a.entreprise_id
where t.a_garder;                                             -- sans doublons ni avis non français

-- 2. AVIS_HEBDO : on supprime l'ancienne vue (colonnes différentes) puis on la recrée
drop view if exists avis_hebdo;

create view avis_hebdo with (security_invoker = true) as
with bornes as (select min(jour) as d0, max(jour) as d1 from avis_analyse)
select
  v.entreprise_id,
  v.nom,
  v.secteur,
  v.semaine,
  count(*)                                                    as nb_avis,
  round(avg(v.note), 2)                                       as note_moyenne,
  round(avg((v.sentiment = 'negatif')::int), 3)               as part_negatifs,
  round(avg(v.a_reponse::int), 3)                             as taux_reponse,
  percentile_cont(0.5) within group (order by v.delai_reponse_heures)
                                                              as delai_reponse_median_h,
  round(avg(v.verifie::int), 3)                               as part_verifies,
  -- semaine entièrement dans la période collectée (pas la 1re ni la dernière, partielles)
  v.semaine >= b.d0 and v.semaine + 6 <= b.d1                 as semaine_complete,
  -- toute la semaine est dans la période de couverture complète
  bool_and(v.dans_couverture)                                 as fiable
from avis_analyse v
cross join bornes b
group by v.entreprise_id, v.nom, v.secteur, v.semaine, b.d0, b.d1;

-- Pour les graphiques : garder les lignes où semaine_complete ET fiable,
-- et afficher nb_avis (peu d'avis = indicateur peu significatif).
