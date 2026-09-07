# Brique 15 — Modèles interchangeables

## Le risque évité

`MODELE_EMB` était déclaré **deux fois** : dans `rag2.py` (requêtes) et dans
`corpus.py` (indexation). En changer un seul produisait un index encodé par un
modèle et des requêtes encodées par un autre.

Les vecteurs auraient vécu dans des espaces différents, la recherche aurait
renvoyé n'importe quoi, **sans jamais lever d'erreur**. Le genre de bug qui
coûte des heures.

## La solution

Tout est centralisé dans `src/config.py`, surchargeable par variable
d'environnement :

```bash
RAG_LLM=llama3.1:8b make ask Q="..."      # aucune ré-indexation
RAG_RERANKER="" make eval-fast            # désactive le reranking
RAG_TOP_K=3 make eval-cat C=docker
RAG_EMB=BAAI/bge-large-en-v1.5 make index # ré-encode obligatoirement
```

`make config` affiche la configuration active et l'état de l'index.

## Coût d'un changement, par étage

| modèle | ré-indexation | durée |
|---|---|---|
| **LLM** (`RAG_LLM`) | non | immédiat |
| **Reranker** (`RAG_RERANKER`) | non | immédiat |
| **Embedding** (`RAG_EMB`) | **oui** | ~2,5 min de GPU |

## Garde-fou

L'index enregistre désormais le modèle qui l'a produit :

```python
{"chunks": [...], "vecteurs": [...],
 "meta": {"embedding": "BAAI/bge-m3", "n_chunks": 1989}}
```

`config.verifier_index()` est appelée au chargement et refuse un index
incohérent avec un message explicite :

```
L'index a été encodé avec « BAAI/bge-m3 »
mais la configuration demande « BAAI/bge-large-en-v1.5 ».
Les vecteurs sont incompatibles.
→ lance `make index` pour ré-encoder le corpus.
```

⚠️ L'index actuel date d'avant cette introduction : il n'a pas de métadonnées
et la vérification le laisse passer. Le prochain `make index` les ajoutera.

## Ce qui reste couplé

Le **prompt système** (`rag2.SYSTEM`) est écrit pour Qwen. Un autre modèle
pourrait demander une formulation différente — notamment les règles de refus,
qui sont déjà le point faible mesuré.

L'**expansion de requête** est un glossaire manuel : indépendant du modèle,
mais lié au domaine du corpus.
