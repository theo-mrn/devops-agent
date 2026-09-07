# Brique 10 — Corpus Terraform élargi + reranker

## 1. L'élargissement Terraform a DÉGRADÉ le retrieval

Corpus : 1028 → **1427 chunks**. Terraform passe de 122 à 521 chunks
(ratio K8s/TF de 7 à 1,7).

| | 1028 chunks | 1427 chunks |
|---|---:|---:|
| Hit Rate fichier | 97% | **94%** ↓ |
| MRR fichier | 0,861 | **0,811** ↓ |

**Ma recommandation était mauvaise.** J'ai supposé qu'équilibrer le corpus
aiderait, sans le vérifier. Effet de dilution : les pages de backend Terraform
parlent toutes de « Data Source Configuration » et se font concurrence.
`tf_state_purpose`, qui passait, devient RATÉ.

## 2. ⚠️ BM25 s'effondre à l'échelle

| corpus | BM25 Hit Rate |
|---|---:|
| 387 chunks (brique 7) | **100%** |
| 1427 chunks (brique 10) | **76%** |

En brique 7, BM25 battait le dense et j'en avais tiré une conclusion générale.
**Elle ne valait qu'à cette échelle.** Sur un gros corpus, les mots fréquents
(« state », « resource », « configuration ») deviennent ambigus et BM25 devient
la pire méthode.

→ **Un résultat mesuré sur un petit corpus ne se transpose pas.**

## 3. Le reranker (bge-reranker-v2-m3)

Schéma en deux étapes : `hybride → 20 candidats → cross-encoder → top 5`.

Différence de nature : dense et BM25 comparent des représentations calculées
**séparément** (le vecteur du document ignore la question). Le cross-encoder lit
la paire (question, document) **ensemble**. Bien plus précis, bien plus lent —
d'où la présélection.

| méthode | Hit Rate | MRR fichier | MRR contenu |
|---|---:|---:|---:|
| dense | 94% | 0,833 | — |
| BM25 seul | 76% | 0,628 | — |
| hybride RRF | 94% | 0,811 | 0,892 |
| **hybride + reranker** | **97%** | 0,822 | **0,919** |

`tf_style` passe du rang 5 au rang 1. Sur le cas 137, le chunk
`interne/exit-codes — Exit code 137 : OOMKilled` remonte en tête à 0,89.

## 4. ⚠️ Régression : les refus tombent de 5/5 à 3/5

Le reranker retient les 5 meilleurs sur 20 et leur donne des scores élevés
**même quand aucun n'est pertinent**. Il classe bien, mais ne dit jamais
« aucun ne convient ». Résultat mesuré :

- `absent_version_moved` → **« disponible depuis la version 0.12.18 »**
  L'hallucination de la baseline, réapparue.
- `absent_ansible` → un playbook Ansible complet inventé à partir de chunks
  Kubernetes.

## 5. ❌ Le garde-fou par seuil — tenté puis ABANDONNÉ

Contrairement au RRF et au cosinus, **les scores du cross-encoder discriminent** :

| | plage |
|---|---|
| questions dans le corpus | 0,745 → 0,975 |
| questions hors corpus | 0,010 → 0,595 |

Écart franc entre 0,595 et 0,745 → **seuil à 0,65**.

C'est le garde-fou abandonné en brique 9, où ni RRF (structurellement : il
calcule à partir de rangs, le 1er obtient toujours 0,0328) ni dense (« Ansible »
scorait plus haut que « bloc moved ») ne séparaient les deux cas.

Vérification :

```
0.292  version du bloc moved   → REFUS
0.097  playbook Ansible        → REFUS
0.952  exit code 137           → répond, chunks interne/exit-codes
```

Bénéfice annexe : le refus court-circuite l'appel au modèle — réponse instantanée.

## Coût

- modèle : ~1,1 Go, téléchargement initial 237 s
- latence : +2 à 3 s par requête (20 paires à scorer)
- l'évaluation complète passe de ~10 à ~14 minutes
