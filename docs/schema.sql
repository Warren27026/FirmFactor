-- =====================================================================
-- FirmFactor — schéma de la base (Supabase / PostgreSQL)
-- Aligné sur le document de cadrage final.
-- ⚠️ La première partie SUPPRIME les anciennes tables (et leurs données).
-- =====================================================================

drop view if exists avis_hebdo;
drop table if exists indicateurs_mensuels, predictions, annotations,
                     avis_traites, analyses_avis, avis, pages_sources,
                     entreprises cascade;

-- ---------------------------------------------------------------------
-- 1. ENTREPRISES — les ~60 entreprises suivies (3 secteurs)
-- ---------------------------------------------------------------------
create table entreprises (
  id                bigint generated always as identity primary key,
  nom               text not null,
  domaine           text not null unique,          -- ex. 'www.cdiscount.com'
  secteur           text not null
                    check (secteur in ('commerce_en_ligne', 'voyages', 'livraison_colis')),
  url_trustpilot    text not null unique,
  note_trustpilot   numeric(2,1),                  -- note globale affichée (TrustScore)
  nb_avis_affiche   integer,                       -- dimension « visibilité »
  avis_6_mois       integer,                       -- vrai nombre d'avis sur 6 mois (filtre Trustpilot)
  pages_6_mois      integer,                       -- pages accessibles sur 6 mois (max 100 sur Trustpilot)
  pages_collectees  integer,                       -- pages réellement collectées
  debut_couverture  date,                          -- date du plus ancien avis collecté
  couverture_complete boolean,                     -- false = plafond de 100 pages atteint
  derniere_collecte timestamptz,
  created_at        timestamptz default now()
);

-- ---------------------------------------------------------------------
-- 2. AVIS — données BRUTES collectées par scraping (étape 1)
--    Exactement les champs du document de cadrage. Aucune donnée
--    d'identité de l'auteur n'est stockée (ni pseudo, ni identifiant).
-- ---------------------------------------------------------------------
create table avis (
  id               bigint generated always as identity primary key,
  entreprise_id    bigint not null references entreprises(id) on delete cascade,
  id_externe       text not null,                  -- id de l'avis : évite les doublons
  note             smallint check (note between 1 and 5),   -- nombre d'étoiles
  titre            text,                           -- titre de l'avis
  texte            text,                           -- commentaire
  date_publication timestamptz,
  date_experience  date,
  verifie          boolean,                        -- avis vérifié ou non
  reponse_texte    text,                           -- réponse du commerçant
  reponse_date     timestamptz,                    -- date de la réponse
  auteur_nb_avis   integer,                        -- nb d'avis déjà publiés par l'auteur
  collecte_le      timestamptz default now(),
  unique (entreprise_id, id_externe)
);

-- ---------------------------------------------------------------------
-- 3. AVIS_TRAITES — résultat du traitement (étape 2)
--    Nettoyage, langue, doublons, anonymisation du texte
--    (e-mails, téléphones, noms cités masqués).
-- ---------------------------------------------------------------------
create table avis_traites (
  avis_id        bigint primary key references avis(id) on delete cascade,
  titre_propre   text,
  texte_propre   text,                             -- texte nettoyé et anonymisé
  langue         text,                             -- détectée automatiquement
  nb_mots        integer,
  est_doublon    boolean default false,            -- même texte publié plusieurs fois
  a_garder       boolean default true,             -- false = exclu des analyses
  traite_le      timestamptz default now()
);

-- ---------------------------------------------------------------------
-- 4. ANNOTATIONS — vérité terrain annotée à la main (étape 2)
-- ---------------------------------------------------------------------
create table annotations (
  avis_id      bigint not null references avis(id) on delete cascade,
  annotateur   text not null check (annotateur in ('warren', 'franck', 'joel')),
  sentiment    text not null check (sentiment in ('positif', 'neutre', 'negatif')),
  aspects      jsonb,          -- ex. {"livraison": "negatif", "prix": "positif"}
  annote_le    timestamptz default now(),
  primary key (avis_id, annotateur)
);

-- ---------------------------------------------------------------------
-- 5. PREDICTIONS — sorties des modèles d'IA (étape 4)
--    Une ligne par avis ET par modèle : permet de comparer les modèles.
-- ---------------------------------------------------------------------
create table predictions (
  avis_id          bigint not null references avis(id) on delete cascade,
  modele           text not null,                  -- ex. 'tfidf-logreg-v1', 'camembert-v1'
  sentiment        text check (sentiment in ('positif', 'neutre', 'negatif')),
  sentiment_score  real,                           -- confiance du modèle (0 à 1)
  aspects          jsonb,
  theme            text,
  predit_le        timestamptz default now(),
  primary key (avis_id, modele)
);

-- ---------------------------------------------------------------------
-- 6. INDICATEURS_MENSUELS — indicateurs de réputation (étape 5)
--    Une ligne par entreprise et par mois : sert aux tendances,
--    aux prévisions et aux tableaux de bord (étape 6).
-- ---------------------------------------------------------------------
create table indicateurs_mensuels (
  entreprise_id         bigint not null references entreprises(id) on delete cascade,
  mois                  date not null,             -- 1er jour du mois
  nb_avis               integer,                   -- visibilité
  note_moyenne          numeric(3,2),              -- satisfaction globale
  ecart_type_notes      numeric(3,2),              -- cohérence
  part_negatifs         numeric(4,3),              -- perception détaillée (IA)
  taux_reponse          numeric(4,3),              -- réactivité
  delai_reponse_jours   numeric(6,1),              -- réactivité
  score_reputation      numeric(5,2),              -- score global sur 100
  calcule_le            timestamptz default now(),
  primary key (entreprise_id, mois)
);

-- ---------------------------------------------------------------------
-- 7. VUE AVIS_HEBDO — volumes par semaine, corrigés de l'échantillonnage
--    Les proportions (note moyenne, % négatifs) restent justes sans correction ;
--    les VOLUMES doivent être multipliés par pages_6_mois / pages_collectees.
-- ---------------------------------------------------------------------
create view avis_hebdo with (security_invoker = true) as
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

-- ---------------------------------------------------------------------
-- Index : accélèrent les requêtes par entreprise et par date
-- ---------------------------------------------------------------------
create index on avis (entreprise_id, date_publication);
create index on predictions (modele);

-- ---------------------------------------------------------------------
-- Sécurité : bloque l'accès public via la clé « publishable/anon ».
-- Nos scripts Python (DATABASE_URL) ne sont pas concernés.
-- On ouvrira la lecture plus tard, table par table, pour l'appli.
-- ---------------------------------------------------------------------
alter table entreprises          enable row level security;
alter table avis                 enable row level security;
alter table avis_traites         enable row level security;
alter table annotations          enable row level security;
alter table predictions          enable row level security;
alter table indicateurs_mensuels enable row level security;
