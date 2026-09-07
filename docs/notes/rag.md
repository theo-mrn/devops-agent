# Brique 4 — RAG complet (recherche → injection → génération)

Pipeline : `chunk_structure` → `bge-m3` (top 3) → `qwen2.5-coder:7b` → réponse.

## Le résultat qui valide tout le projet

| | Baseline (brique 1) | Avec RAG |
|---|---|---|
| **Q3 — version du bloc `moved`** | « introduit dans Terraform **0.12.13** » *(inventé)* | « Le contexte fourni ne contient pas cette information. » |
| **Q2 — guillemets sur `from`** | `from = "ancien_chemin"` *(faux)* | « Non, `from` et `to` ne prennent pas de guillemets. » ✅ |

Le modèle est passé d'une **affirmation fausse et assurée** à un **aveu
d'ignorance honnête**. C'est l'objectif fixé en fin de brique 1, atteint.

Détail important : sur Q3 le chunk retenu avait un score de **0,611**, plus
élevé que sur Q1. Le retrieval était « confiant » et pourtant sans la réponse.
**C'est le prompt qui a évité l'hallucination, pas la recherche.**

## Le prompt : les trois règles qui font tout

1. Répondre UNIQUEMENT à partir du contexte.
2. Ne JAMAIS compléter avec les connaissances générales.
3. Autoriser explicitement « Le contexte fourni ne contient pas cette information ».

Sans la règle 3, le modèle hallucine **avec une source à l'appui** — pire
qu'une hallucination nue, parce qu'elle est crédible.

## Contrôle négatif

Question hors corpus (« readiness probe Kubernetes ») → refus correct.
Scores plafonnant à **0,424** contre 0,611 pour une question pertinente :
le signal de non-pertinence existe et pourrait servir de garde-fou.

## Coût réel mesuré

| | sans RAG | avec RAG |
|---|---:|---:|
| tokens en entrée | 41 | 544 |
| tokens en sortie | 378 | 89 |
| recherche | — | 1,09 s |
| génération | 13,63 s | 3,73 s |
| **TOTAL** | **13,63 s** | **4,82 s** |

**Le RAG est ~3× plus RAPIDE**, malgré 13× plus de tokens en entrée.

Pourquoi : sans contexte le modèle brode (378 tokens) ; avec contexte il répond
et s'arrête (89 tokens). La lecture du contexte se parallélise, la génération
non — chaque token dépend du précédent.

→ Le coût du RAG n'est pas le temps de réponse, c'est la **fenêtre de contexte**.
Avec 20 chunks au lieu de 3, la RAM sature avant qu'on sente un ralentissement.

## Limite constatée sur Q1

Le modèle a repris les placeholders `<ancien chemin>` au lieu de l'exemple
concret `from = aws_instance.a`. Cause : le chunk `Example` n'est pas dans le
top 3 — la question dit « syntaxe », le chunk s'intitule « Example », et
l'embedding ne fait pas le lien.

→ Même limite qu'en brique 3 : le dense seul ne suffit pas. Argument pour
l'hybride BM25 et le reranker.
