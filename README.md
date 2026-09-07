# devops-agent

Assistant DevOps qui répond sur Kubernetes, Terraform, Docker, Linux et CI/CD
en citant ses sources, et qui peut observer un cluster réel **en lecture seule**
pour établir un diagnostic.

## Ce que c'est

Deux modes :

- **RAG** — une question, une réponse sourcée depuis la documentation indexée.
- **Agent** — le modèle enchaîne des outils (recherche documentaire, `kubectl`,
  lecture de manifests) pour diagnostiquer un problème réel.

Le modèle de génération est interchangeable : local (Ollama) ou API (Claude).
Le retrieval reste local dans les deux cas.

## Installation

```bash
make install          # uv sync
make fetch            # télécharge les sources documentaires
make index            # construit l'index vectoriel
```

## Usage

```bash
make ask Q="Que signifie exit code 137 ?"
make diagnose Q="Un pod crashe en prod, pourquoi ?"   # agent, API requise
make test                                              # 54 tests, instantanés
make eval-fast                                         # qualité du retrieval
```

## Architecture

```
question
   ↓
expansion      glossaire français → vocabulaire technique
   ↓
retrieval      dense (bge-m3) + BM25, fusionnés par RRF
   ↓
rerank         cross-encoder sur 20 candidats → 5
   ↓
generation     Qwen local ou Claude API
   ↓
réponse sourcée
```

En mode agent, le RAG devient un outil parmi d'autres, et le modèle décide
lui-même lesquels appeler.

## Structure

```
src/devops_agent/
├── core/          configuration centrale
├── ingestion/     sources, chunking, index
├── retrieval/     lexical, fusion, rerank, expansion, pipeline
├── generation/    fournisseurs de LLM
├── agent/         boucle tool-use, outils
└── evaluation/    mesure de qualité

tests/             garde-fous de sûreté et logique métier
eval/              115 cas de test
data/raw/          documentation téléchargée
data/interne/      documents rédigés (codes de sortie, diagnostic)
docs/notes/        journal des décisions techniques
scripts/archive/   démonstrations pédagogiques
```

## Configuration

Tout est surchargeable par variable d'environnement :

```bash
RAG_PROVIDER=anthropic make ask Q="..."     # API au lieu d'Ollama
RAG_LLM_API=claude-sonnet-5 make diagnose Q="..."
RAG_NAMESPACES=prod,staging make diagnose Q="..."   # restreint kubectl
RAG_TOP_K=3 make eval-fast
```

`make config` affiche la configuration active.

## Sûreté

L'agent est en **lecture seule stricte**, garanti par le code et non par le
prompt :

- verbes `kubectl` sur liste blanche (`get`, `describe`, `logs`, `top`, …)
- accès aux Secrets interdit, même en lecture
- lecture de fichiers confinée au dépôt configuré
- namespaces restreignables

Ces règles sont couvertes par 54 tests unitaires exécutés en 0,03 s.

## Qualité mesurée

115 cas de test, trois critères mesurés séparément :

| | |
|---|---:|
| Retrieval (Hit Rate @5) | 100% |
| Précision | 94% |
| Méthode | 100% |
| Sûreté | 100% |

Voir `docs/notes/` pour le détail des mesures et des décisions.
