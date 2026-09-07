# Brique 5 — Évaluation

Jeu de test : `eval/questions.yaml` — **7 cas**, dont **2 de type « absent »**
(la réponse n'est PAS dans le corpus : le système doit refuser).

Un jeu qui ne teste que les questions solubles récompense un modèle bavard.
Il faut mesurer autant la capacité à **se taire**.

## Principe : mesurer retrieval et génération SÉPARÉMENT

Un score global ne dit pas où est le problème.

- **Retrieval** — Hit Rate @K (le bon chunk est-il dans le top-K ?)
  et MRR (à quel rang ? 1er = 1.0, 2e = 0.5, 3e = 0.33).
- **Génération** — la réponse contient-elle ce qu'il faut,
  et surtout **pas** ce qu'il ne faut pas ?

## Score de référence

```
Hit Rate @3        100%
MRR                1.000
Génération         7/7 (100%)
  dont refus       2/2   (anti-hallucination)
```

⚠️ **Ce 100% ne prouve pas que le système est bon.** Avec 5 chunks et un top-3,
on retient 60% du corpus à chaque requête. C'est un **plancher anti-régression**,
pas une preuve de qualité. Le score deviendra informatif à 500 chunks.

## Incident révélateur : un test raté ≠ un système raté

Premier passage : 6/7. Le cas `from_obligatoire` échouait.
La réponse du modèle était : « Oui, les arguments `from` et `to` sont
**obligatoires** » — parfaitement correcte. Mon test exigeait le mot anglais
`required` alors que le modèle répond en français.

→ **Toujours lire la réponse avant de conclure.** Un pipeline qui affiche un
score sans montrer les réponses est un piège. Correction : `doit_contenir`
accepte désormais des listes d'alternatives (« au moins un de ces termes »).

## Ablation — effet du chunking sur le retrieval

| stratégie | Hit Rate | MRR | chunks |
|---|---:|---:|---:|
| structurel | 60% | 0,600 | 5 |
| naïf 500 car. | 60% | 0,600 | 6 |
| naïf 200 car. | **40%** | **0,400** | 14 |
| naïf 1000 car. | 60% | 0,467 | 3 |

### ⚠️ Correction de ce qui était affirmé en brique 2

**Le structurel n'est PAS meilleur que le naïf 500 sur le retrieval** — les deux
font 60% / 0,600. L'affirmation de la brique 2 était une inférence à partir du
nombre de chunks cassés, pas une mesure.

Ce que l'ablation établit réellement :
- **200 car. dégrade nettement** — chunks trop petits, contexte perdu.
- **1000 car. : même Hit Rate, MRR plus bas** — le bon chunk remonte, mais
  moins souvent en 1re position.

### Le vrai bénéfice du chunking structurel

Il n'est **pas dans le retrieval, il est dans la génération**. Le naïf remonte
le bon chunk aussi souvent, mais **abîmé** (tableau décapité, phrase coupée).
Le retrieval ne voit pas la différence ; le modèle qui lit le chunk, si.

La justification de la brique 2 tient, mais pour une autre raison que celle
avancée à l'époque.

## Note sur les scores absolus

L'ablation donne 60% là où `evaluer.py` donne 100% : les deux ne mesurent pas
la même chose. `evaluer.py` vérifie le **titre** du chunk attendu ; l'ablation
vérifie que le chunk **contient les termes de la réponse** (nécessaire pour
comparer des découpages sans titres).

→ **Une métrique ne se lit jamais sans sa définition.**
