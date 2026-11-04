-- WARNING: This schema is for context only and is not meant to be run.
-- Table order and constraints may not be valid for execution.

CREATE TABLE public.entreprises (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  nom text NOT NULL,
  domaine text NOT NULL UNIQUE,
  secteur text NOT NULL CHECK (secteur = ANY (ARRAY['commerce_en_ligne'::text, 'voyages'::text, 'livraison_colis'::text])),
  url_trustpilot text NOT NULL UNIQUE,
  note_trustpilot numeric,
  nb_avis_affiche integer,
  derniere_collecte timestamp with time zone,
  created_at timestamp with time zone DEFAULT now(),
  CONSTRAINT entreprises_pkey PRIMARY KEY (id)
);
CREATE TABLE public.avis (
  id bigint GENERATED ALWAYS AS IDENTITY NOT NULL,
  entreprise_id bigint NOT NULL,
  id_externe text NOT NULL,
  note smallint CHECK (note >= 1 AND note <= 5),
  titre text,
  texte text,
  date_publication timestamp with time zone,
  date_experience date,
  verifie boolean,
  reponse_texte text,
  reponse_date timestamp with time zone,
  auteur_nb_avis integer,
  collecte_le timestamp with time zone DEFAULT now(),
  CONSTRAINT avis_pkey PRIMARY KEY (id),
  CONSTRAINT avis_entreprise_id_fkey FOREIGN KEY (entreprise_id) REFERENCES public.entreprises(id)
);
CREATE TABLE public.avis_traites (
  avis_id bigint NOT NULL,
  titre_propre text,
  texte_propre text,
  langue text,
  nb_mots integer,
  est_doublon boolean DEFAULT false,
  a_garder boolean DEFAULT true,
  traite_le timestamp with time zone DEFAULT now(),
  CONSTRAINT avis_traites_pkey PRIMARY KEY (avis_id),
  CONSTRAINT avis_traites_avis_id_fkey FOREIGN KEY (avis_id) REFERENCES public.avis(id)
);
CREATE TABLE public.annotations (
  avis_id bigint NOT NULL,
  annotateur text NOT NULL CHECK (annotateur = ANY (ARRAY['warren'::text, 'franck'::text, 'joel'::text])),
  sentiment text NOT NULL CHECK (sentiment = ANY (ARRAY['positif'::text, 'neutre'::text, 'negatif'::text])),
  aspects jsonb,
  annote_le timestamp with time zone DEFAULT now(),
  CONSTRAINT annotations_pkey PRIMARY KEY (avis_id, annotateur),
  CONSTRAINT annotations_avis_id_fkey FOREIGN KEY (avis_id) REFERENCES public.avis(id)
);
CREATE TABLE public.predictions (
  avis_id bigint NOT NULL,
  modele text NOT NULL,
  sentiment text CHECK (sentiment = ANY (ARRAY['positif'::text, 'neutre'::text, 'negatif'::text])),
  sentiment_score real,
  aspects jsonb,
  theme text,
  predit_le timestamp with time zone DEFAULT now(),
  CONSTRAINT predictions_pkey PRIMARY KEY (avis_id, modele),
  CONSTRAINT predictions_avis_id_fkey FOREIGN KEY (avis_id) REFERENCES public.avis(id)
);
CREATE TABLE public.indicateurs_mensuels (
  entreprise_id bigint NOT NULL,
  mois date NOT NULL,
  nb_avis integer,
  note_moyenne numeric,
  ecart_type_notes numeric,
  part_negatifs numeric,
  taux_reponse numeric,
  delai_reponse_jours numeric,
  score_reputation numeric,
  calcule_le timestamp with time zone DEFAULT now(),
  CONSTRAINT indicateurs_mensuels_pkey PRIMARY KEY (entreprise_id, mois),
  CONSTRAINT indicateurs_mensuels_entreprise_id_fkey FOREIGN KEY (entreprise_id) REFERENCES public.entreprises(id)
);