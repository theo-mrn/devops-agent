# Brique 3 — Embeddings et recherche sémantique

Modèle : `BAAI/bge-m3` · 568M paramètres · vecteurs de **1024 dimensions**
Device : MPS (Metal) · chargement à froid 6,6 s · 6 phrases encodées en 0,45 s

## Matrice de similarité — le résultat inattendu

| paire | score | attendu |
|---|---:|---|
| 3↔4 · « déplacer une ressource Terraform » / « bloc moved change l'adresse » | **0,615** | élevée ✅ |
| 0↔2 · « exit code 137 » / « ImagePullBackOff » | **0,565** | basse ❌ |
| 0↔1 · « exit code 137 » / « OOMKilled » | **0,517** | élevée ❌ |
| 0↔5 · « exit code 137 » / « tarte aux pommes » | 0,321 | basse ✅ |

**Le modèle classe ImagePullBackOff PLUS proche d'« exit 137 » qu'OOMKilled.**

Or 137 = SIGKILL = OOMKilled, tandis qu'ImagePullBackOff n'a aucun rapport.
Explication : l'embedding capte le **domaine** (« une panne de pod », le mot
« pod » partagé), pas la **relation causale**. Savoir que 137 signifie OOM est
de la connaissance d'expert qu'un modèle d'embedding généraliste n'a pas.

### Trois conséquences

1. **Les valeurs absolues ne veulent rien dire.** Seul le classement compte.
   Ne jamais poser de seuil du type « garder au-dessus de 0,5 » : ici cela
   garderait le mauvais résultat.
2. **La recherche vectorielle seule ne suffit pas** → justifie l'hybride
   BM25 + dense annoncé dans le README. Validé empiriquement, pas sur parole.
3. **C'est la raison d'être du reranker** (phase 5) : lui compare question et
   document *ensemble* et sait trancher ces cas.

## Recherche sur les 5 chunks du document

| question | chunk retenu | verdict |
|---|---|---|
| syntaxe du bloc moved | `Specification` (tableau complet) | ✅ |
| `from` prend des guillemets ? | `Configuration model` → `reference \| required` | ✅ corrige l'erreur de la baseline |
| depuis quelle version ? | `moved block reference` (0,589) | ❌ **la réponse n'est nulle part** |

## ⚠️ La leçon principale

**Le RAG ne remonte jamais « rien ». Il remonte toujours le moins mauvais.**

Question 3 : score de 0,589, qui a l'air d'une bonne trouvaille — mais il mesure
une proximité *relative entre 5 chunks*, pas la présence de l'information.
Cette page ne mentionne nulle part Terraform 1.1.

Envoyer ce chunk au modèle en lui demandant la version le ferait halluciner
**avec une source à l'appui** — pire qu'une hallucination nue.

### Deux exigences qui en découlent pour la suite

- Le corpus doit inclure les **release notes**, pas seulement la doc de référence.
- Le prompt doit **autoriser explicitement** le modèle à répondre
  « le contexte fourni ne contient pas cette information ».

## Note mémoire (16 Go)

`bge-m3` en float32 ≈ 2,5 Go en RAM. Avec Qwen (4,4 Go) : ~7 Go cumulés.
Tient confortablement, mais ne pas garder les deux chargés inutilement.
