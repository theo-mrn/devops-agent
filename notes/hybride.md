# Brique 7 — Recherche hybride (dense + BM25, fusion RRF)

## Le résultat

| méthode | Hit Rate @3 | MRR |
|---|---:|---:|
| dense seul (bge-m3) | 90% | 0,850 |
| **BM25 seul** | **100%** | 0,883 |
| **hybride RRF** | **100%** | **0,933** |

**+10 points de Hit Rate, +0,083 de MRR.** L'échec `comportement_state`
documenté en brique 6 est éliminé. Le pari du README est validé — mesuré,
pas supposé.

## ⚠️ Résultat contre-intuitif : BM25 seul bat le dense

100% contre 90%. Une méthode de **1994**, sans réseau de neurones, fait mieux
qu'un modèle de 568 M de paramètres.

Ce n'est pas un hasard : sur de la documentation technique, le vocabulaire est
précis et les identifiants exacts nombreux (`moved`, `OOMKilled`,
`ImagePullBackOff`). C'est le régime où BM25 excelle.

→ Beaucoup de projets RAG sautent au vectoriel sans jamais tester cette
baseline. **Toujours mesurer BM25 seul avant de conclure.**

## Les poids ne changent rien

| pondération | MRR |
|---|---:|
| 1:1 | 0,933 |
| 2:1, 1:2, 3:1, 1:3 | 0,933 |

Identique dans tous les cas. Avec 10 cas de test, l'ordre RRF est trop stable
pour que les poids pèsent.

→ **Le jeu d'évaluation est trop petit pour régler des hyperparamètres.**
Utile à savoir avant de perdre du temps à optimiser dans le vide.

## Pourquoi RRF plutôt qu'une somme pondérée

Les scores ne sont pas comparables : BM25 va de 0 à ~15 sans borne, le cosinus
de 0 à 1. Les **rangs**, eux, le sont.

    score_RRF(doc) = Σ  poids / (k + rang_dans_cette_méthode)      k = 60

## Défauts observés du RRF

**Il récompense le consensus mou.** Sur la question `moved`, un chunk classé
`bm25#8 dense#5` — deux rangs médiocres — a battu un chunk `moved` bien classé
par une seule méthode.

**Il peut dégrader.** Sur « exit code 137 », le dense trouvait
`determine-reason-pod-failure` en 1re position ; l'hybride le relègue en 3e
parce que BM25 vote massivement pour `pod-lifecycle` (« exit » et « code » y
sont fréquents).

→ Le gain global est net, mais l'hybride n'améliore pas *chaque* requête.

## Ce que NI l'un NI l'autre ne résout

« exit code 137 » ne remonte toujours pas les sources OOM en priorité.
Ni l'embedding ni BM25 ne connaissent la chaîne 137 → SIGKILL → OOMKilled :
c'est de la **connaissance métier**, absente des deux mécanismes.

→ C'est le travail d'un **reranker** (brique suivante) ou d'une expansion de
requête.

## Tokenisation : le point délicat en DevOps

`ImagePullBackOff`, `aws_instance.a`, `--previous` ne se découpent pas comme du
texte ordinaire. La tokenisation retenue produit **trois formes** :
1. le token entier (`aws_instance.a`),
2. les sous-mots séparés par `_ - . / :` (`aws`, `instance`),
3. la décomposition CamelCase (`ImagePullBackOff` → `image`, `pull`, `back`, `off`).

**Bug corrigé** : les accents cassaient la regex — « détruit-il » produisait
« truit-il ». Normalisation NFD appliquée avant découpage.

## Faux positif de la brique 6 : résolu

Avant : « Non, Terraform ne détruit pas la ressource quand on utilise
`terraform state mv` » — hors sujet, mais le test cherchait « non » et passait.

Après : « Non, Terraform ne détruit pas la ressource quand on utilise le bloc
`moved` ». Bonne réponse, bonnes sources (`moved`, `moved`, `refactor`).

## Score de référence

```
Hit Rate @3    100%
MRR            0.933
Génération     12/12
  dont refus   2/2
```
