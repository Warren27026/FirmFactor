# KPI FirmFactor

Deux utilisateurs : le **client** (il choisit une entreprise) et le **commerçant** (il pilote sa réputation).
Règles communes : afficher la période et le nombre d'avis derrière chaque chiffre ; ne rien afficher sous le seuil `fiable` ; afficher la part d'avis écartés du sentiment (langue étrangère).

Filtres : **note, vérifiés, réponses, délais = `a_garder`** (81 133 avis) ; **sentiment, thèmes, ton = `a_garder_nlp`** (81 014 avis, français).
Étape 1 = calculable maintenant (vues existantes). Étape 2 = après entraînement du modèle.

## 1. KPI client

| KPI | Définition / formule | Source | Étape |
|---|---|---|---|
| Note moyenne récente | moyenne des étoiles, 90 derniers jours | `avis_analyse` | 1 |
| Tendance | note 90 j moins note des 90 j précédents | `avis_analyse` | 1 |
| Part d'avis vérifiés | avis `verifie` / total | `avis_analyse` | 1 |
| Position dans le secteur | rang de l'entreprise (note ou score) dans son secteur | `indicateurs_mensuels` | 1 |
| Score de réputation 0-100 | 45 % note, 25 % sentiment, 15 % réactivité, 15 % ton des réponses ; lissé sur petit échantillon | `indicateurs_mensuels.score_reputation` | 2 |
| Points forts / faibles | thèmes les plus cités en positif / négatif | `predictions` | 2 |

## 2. KPI commerçant

| KPI | Définition / formule | Source | Étape |
|---|---|---|---|
| Taux de réponse | avis avec réponse / total | `avis_analyse.a_reponse` | 1 |
| Délai de réponse médian | médiane de `delai_reponse_heures` | `avis_analyse` | 1 |
| Part de négatifs | avis ≤ 2 étoiles / total, par semaine | `avis_hebdo` | 1 |
| Alerte pic négatif | semaine dont la part de négatifs dépasse nettement la moyenne (si `fiable`) | `avis_hebdo` | 1 |
| Thèmes négatifs principaux | classement des sujets de plainte | `predictions` | 2 |
| Qualité et ton des réponses | score de ton sur `reponse_propre` (29 754 réponses) | `predictions` | 2 |

## 3. KPI du modèle (équipe)

| KPI | Définition | Source / filtre |
|---|---|---|
| F1-macro | moyenne des F1 des 3 classes | `jeu='test'` et `a_garder_nlp` (7 978 avis) |
| F1 par classe (neutre à part) | F1 négatif, neutre, positif ; le neutre = 5-6 % des avis, donc instable | idem |
| Matrice de confusion | erreurs par paire de classes | idem |
| Kappa de Cohen, bruit d'étiquettes | accord annotation manuelle vs étoiles | `annotations` (200-500 avis) |
| Résultats par secteur | F1 par secteur | `v_entrainement` |

`validation` sert à régler le modèle, jamais à le noter.
