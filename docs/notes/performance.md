# Performance de l'évaluation

## Le problème

L'évaluation complète coûtait 45 min : 115 générations LLM à ~25 s chacune,
strictement séquentielles (`OLLAMA_NUM_PARALLEL` vaut 1 par défaut).

## Parallélisation : 1,48x, pas 8x

`OLLAMA_NUM_PARALLEL=3` dans le plist du service, plus un `ThreadPoolExecutor`
dans l'évaluateur.

| test | accélération |
|---|---:|
| prompts minuscules (« compte de 1 à 5 ») | 8,06x |
| **prompts réels (2300 tokens de contexte)** | **1,48x** |

L'écart vient de la saturation GPU : sur des prompts courts la latence domine
et le parallélisme masque tout ; sur des prompts réels le GPU est déjà à 100%
pour une seule requête. **Sur un M2 Pro, le GPU est la limite physique.**

→ 45 min deviennent ~30 min. Utile, pas révolutionnaire.

## Deux bugs de concurrence corrigés

**1. Triple chargement des modèles.** Chaque thread appelait `charger()` et
chargeait sa propre copie de bge-m3 — trois modèles en mémoire, saturation, mort
du script sans message. Correction : verrou sur le cache dans `rag2`, `reranker`
et `bm25`, plus un préchargement avant le lancement des threads.

**2. Metal n'est pas thread-safe.**

```
failed assertion 'A command encoder is already encoding to this command buffer'
```

Deux threads encodant simultanément sur MPS font échouer le pilote. Correction :
verrou GPU global autour du retrieval. Coût acceptable — mesuré, le retrieval
ne représente que **8% du temps** (2,4 s contre 25,8 s pour la génération).

## Ce qui fait vraiment gagner du temps

| commande | durée | usage |
|---|---:|---|
| `make eval-fast` | **~3 min** | 80% des décisions : corpus, chunking, méthode de recherche |
| `make eval-cat C=docker` | **~2 min** | vérifier un domaine précis |
| `make eval` | ~30 min | fin de chantier uniquement |

Le retrieval est ce qui bouge quand on modifie le pipeline. La génération ne
change qu'avec le prompt ou le modèle — inutile de la remesurer à chaque essai.
