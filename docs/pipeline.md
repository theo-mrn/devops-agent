# Pipeline

```
telecharger.py          récupère la documentation (K8s, Terraform, Docker, CI/CD)
        ↓
corpus.py               découpe et encode → data/index/corpus.pkl
   └── chunk_structure.py   découpage aux frontières sémantiques
        ↓
rag2.py                 point d'entrée : question → réponse sourcée
   ├── expansion.py         glossaire français → termes du corpus
   ├── hybride.py           fusion RRF de deux recherches
   │     ├── (dense)        bge-m3, via rag2.chercher_dense
   │     └── bm25.py        recherche par mots-clés exacts
   └── reranker.py          cross-encoder, réordonne les 20 candidats
        ↓
evaluer3.py             mesure : retrieval + précision / méthode / sûreté
```

## Scripts

| script | rôle |
|---|---|
| `telecharger.py` | constitue le corpus depuis GitHub |
| `corpus.py` | chunking + embeddings → index |
| `chunk_structure.py` | découpage par section, blocs de code atomiques |
| `rag2.py` | pipeline complet, `chercher_rerank()` est le mode par défaut |
| `expansion.py` | glossaire + garde-fou de domaine |
| `hybride.py` | RRF sur dense + BM25 |
| `bm25.py` | BM25 avec tokenisation adaptée au DevOps |
| `reranker.py` | bge-reranker-v2-m3 |
| `evaluer3.py` | évaluation sur `eval/questions.yaml` |
| `ablation2.py` | compare dense / BM25 / hybride / reranker |
| `generer_dataset.py` | dataset de fine-tuning (chantier en pause) |

`archive/` contient les démonstrations des briques 1 à 3 — voir son README.
