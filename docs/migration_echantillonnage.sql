-- À lancer UNE FOIS dans Supabase (SQL Editor) si les tables existent déjà.
-- N'efface aucune donnée.
alter table entreprises
  add column if not exists note_trustpilot numeric(2,1),
  add column if not exists avis_6_mois integer,
  add column if not exists pages_6_mois integer,
  add column if not exists pages_collectees integer,
  add column if not exists debut_couverture date,
  add column if not exists couverture_complete boolean;

create or replace view avis_hebdo with (security_invoker = true) as
select
  e.id                                         as entreprise_id,
  e.nom,
  e.secteur,
  date_trunc('week', a.date_publication)::date as semaine,
  count(*)                                     as avis_collectes,
  round(count(*) * coalesce(e.pages_6_mois::numeric / nullif(e.pages_collectees, 0), 1))
                                               as avis_estimes,
  round(avg(a.note), 2)                        as note_moyenne,
  round(avg((a.note <= 2)::int), 3)            as part_notes_basses,
  round(avg((a.reponse_texte is not null)::int), 3) as taux_reponse
from avis a
join entreprises e on e.id = a.entreprise_id
group by e.id, e.nom, e.secteur, semaine;
