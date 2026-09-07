# Brique 16 — Générateur interchangeable (local ou API)

## Principe

Une couche `src/generateur.py` masque le fournisseur. Le pipeline appelle
`generer(system, prompt)` sans savoir d'où vient la réponse.

```bash
make ask Q="..."                              # Ollama, défaut
RAG_PROVIDER=anthropic make ask Q="..."       # API Claude
RAG_PROVIDER=anthropic RAG_LLM_API=claude-sonnet-5 make ask Q="..."
```

## Ce qui change, ce qui ne change pas

| étage | local | API |
|---|---|---|
| chunking | local | local |
| embeddings (bge-m3) | local | **local** |
| BM25 | local | local |
| reranker | local | **local** |
| **génération** | Qwen 7B | **Claude** |

**Le corpus n'est jamais envoyé en entier.** L'indexation reste locale.
⚠️ En revanche, les 5 chunks retenus partent dans le prompt **à chaque
requête** — à peser si la documentation devient confidentielle.

## Détails d'implémentation

**Cache du prompt système.** Le system prompt fait ~400 tokens et ne change
jamais : il est marqué `cache_control: {"type": "ephemeral"}`, ce qui fait
tomber les lectures suivantes à ~10 % du prix.

**Refus de sécurité.** L'API peut renvoyer `stop_reason: "refusal"` avec un
`content` vide. Le lire sans vérifier lèverait une exception — le code teste
`stop_reason` avant de lire les blocs.

**Réponse normalisée.** Les deux fournisseurs renvoient un objet `Reponse`
(texte, tokens d'entrée, tokens de sortie), donc l'appelant est identique.

## Coût estimé

Le jeu d'évaluation fait 115 cas × ~2500 tokens d'entrée et ~300 de sortie.

| modèle | entrée $/M | sortie $/M | une évaluation complète |
|---|---:|---:|---:|
| Qwen local | 0 | 0 | **0 $** (~35 min) |
| claude-haiku-4-5 | 1,00 | 5,00 | ~0,46 $ |
| claude-sonnet-5 | 2,00 | 10,00 | ~0,92 $ |
| claude-opus-5 | 5,00 | 25,00 | ~2,30 $ |

Le cache du prompt système réduit ces montants d'environ 15 %.

## Intérêt pour le projet

Les deux hallucinations qui résistent (`absent_version_moved`,
`absent_ansible`) relèvent du **suivi d'instructions** : refuser quand le
contexte est thématiquement proche mais factuellement muet. Ni le prompt
durci (brique 9) ni un seuil de score (brique 10) ne les corrigent.

Un modèle plus fort sur le suivi d'instructions pourrait les traiter sans
fine-tuning. C'est mesurable en une évaluation.

## Authentification

Le SDK résout les identifiants dans cet ordre : `ANTHROPIC_API_KEY`, puis
`ANTHROPIC_AUTH_TOKEN`, puis un profil créé par `ant auth login`. Aucun
identifiant n'est stocké dans le projet.
