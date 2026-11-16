"""Prépare des lots CSV pour importer les avis via le Table Editor Supabase.

La correspondance id/domaine provient d'un export SQL du projet cible :
    SELECT id, domaine FROM public.entreprises ORDER BY id;
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from scripts.importer_avis_6mois_supabase import AVIS, ROOT, verifier


OUTPUT_COLUMNS = (
    "entreprise_id", "id_externe", "note", "titre", "texte",
    "date_publication", "date_experience", "verifie", "reponse_texte",
    "reponse_date", "auteur_nb_avis", "collecte_le",
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "import_supabase")
    parser.add_argument("--rows-per-file", type=int, default=10000)
    args = parser.parse_args()
    if args.rows_per_file < 1:
        parser.error("--rows-per-file doit être positif")
    entreprises, _, counts, _, _ = verifier(AVIS)
    with args.mapping.open(encoding="utf-8-sig", newline="") as stream:
        mapping_rows = list(csv.DictReader(stream))
    mapping = {row["domaine"]: int(row["id"]) for row in mapping_rows}
    if len(mapping) != len(mapping_rows) or set(mapping) != {row["domaine"] for row in entreprises}:
        raise ValueError("La correspondance id/domaine ne contient pas exactement les 60 entreprises")
    if len(set(mapping.values())) != len(mapping):
        raise ValueError("Identifiants d'entreprise dupliqués")

    args.output.mkdir(parents=True, exist_ok=True)
    written = 0
    file_number = 0
    destination = None
    with AVIS.open(encoding="utf-8-sig", newline="") as source:
        try:
            for row in csv.DictReader(source):
                if written % args.rows_per_file == 0:
                    if destination:
                        destination.close()
                    file_number += 1
                    path = args.output / f"avis_lot_{file_number:02}.csv"
                    destination = path.open("w", encoding="utf-8", newline="")
                    writer = csv.DictWriter(destination, fieldnames=OUTPUT_COLUMNS)
                    writer.writeheader()
                output = {column: row[column] for column in OUTPUT_COLUMNS if column not in {"entreprise_id", "collecte_le"}}
                output["entreprise_id"] = mapping[row["domaine"]]
                output["collecte_le"] = row["date_collecte"]
                writer.writerow(output)
                written += 1
        finally:
            if destination:
                destination.close()
    if written != sum(counts.values()):
        raise ValueError("Le nombre d'avis préparés est incohérent")
    print(f"{written:,} avis répartis dans {file_number} fichiers CSV, dans {args.output}")


if __name__ == "__main__":
    main()
