"""Enregistre les entreprises et les avis dans Supabase."""
import os

import psycopg

SQL_ENTREPRISE = """
insert into entreprises (nom, domaine, secteur, url_trustpilot,
                         note_trustpilot, nb_avis_affiche, derniere_collecte)
values (%(nom)s, %(domaine)s, %(secteur)s, %(url_trustpilot)s,
        %(note_trustpilot)s, %(nb_avis_affiche)s, now())
on conflict (domaine) do update set
    note_trustpilot = excluded.note_trustpilot,
    nb_avis_affiche = excluded.nb_avis_affiche,
    derniere_collecte = now()
returning id
"""

SQL_AVIS = """
insert into avis (entreprise_id, id_externe, note, titre, texte,
                  date_publication, date_experience, verifie,
                  reponse_texte, reponse_date, auteur_nb_avis)
values (%(entreprise_id)s, %(id_externe)s, %(note)s, %(titre)s, %(texte)s,
        %(date_publication)s, %(date_experience)s, %(verifie)s,
        %(reponse_texte)s, %(reponse_date)s, %(auteur_nb_avis)s)
on conflict (entreprise_id, id_externe) do nothing
"""


class SupabasePipeline:
    def open_spider(self, spider):
        self.conn = psycopg.connect(os.environ["DATABASE_URL"], autocommit=True)
        self.ids = {}  # domaine -> id de l'entreprise
        self.nb_avis = 0

    def close_spider(self, spider):
        spider.logger.info("✅ %s avis envoyés à Supabase", self.nb_avis)
        self.conn.close()

    def process_item(self, item, spider):
        if item["type"] == "entreprise":
            self.ids[item["domaine"]] = self.conn.execute(SQL_ENTREPRISE, item).fetchone()[0]
        else:
            item["entreprise_id"] = self.ids[item["domaine"]]
            self.conn.execute(SQL_AVIS, item)
            self.nb_avis += 1
        return item
