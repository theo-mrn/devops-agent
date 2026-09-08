# Vérification mécanique

## Le choix : mécanique plutôt que confirmateur LLM

k-AI ajoute un pipeline Investigateur → Confirmateur → Réconciliation : un
second modèle reçoit les observations brutes sans voir le raisonnement du
premier, un troisième arbitre.

L'idée est solide — elle casse le biais d'ancrage d'un agent qui génère une
hypothèse puis cherche à la confirmer. Mais elle coûte **×2 à ×3 par
diagnostic** (0,030 $ → 0,075 $ sur Sonnet), fait passer la latence de 35 s à
90 s, et **rien ne mesure son gain réel** : le champ `confidence` de k-AI est
déclaratif, un jugement du modèle sur lui-même.

Choix retenu : la vérification mécanique.

| | confirmateur LLM | vérification mécanique |
|---|---|---|
| attrape | erreurs de **raisonnement** | erreurs de **fait** |
| coût | ×2 à ×3 | **zéro token** |
| latence | +55 s | +1 s |
| déterministe | non | oui |

Sur du diagnostic Kubernetes, les erreurs de fait sont les plus fréquentes et
les plus dommageables : un correctif fondé sur une limite mémoire erronée est
inapplicable.

## Ce qui est vérifié

Le module extrait les affirmations vérifiables du rapport et les confronte au
cluster :

```
« limite mémoire : 512Mi »  →  kubectl get pod -o jsonpath  →  2Gi
✗ limite mémoire : annoncé 512Mi, réel 2Gi
```

| affirmation | contrôle |
|---|---|
| limite mémoire d'un pod | `.spec.containers[0].resources.limits.memory` |
| image d'un pod | `.spec.containers[0].image` |
| replicas d'un deployment | `.spec.replicas` |
| sélecteur d'un service | compte les pods portant réellement ces labels |

## Normalisation des unités

`2Gi` et `2048Mi` désignent la même valeur : contredire un modèle qui emploie
une autre unité serait un faux positif. Les quantités Kubernetes sont
normalisées avant comparaison (Ki/Mi/Gi/Ti, K/M/G/T, millicores).

## Trois verdicts

- **✓ confirme** — le rapport dit vrai
- **✗ contredit** — le rapport dit faux, avertissement affiché
- **! constat** — un fait établi par le contrôle, qui n'accuse pas le rapport
- **? invérifiable** — le cluster n'a pas répondu

## Résultat sur le cluster réel

Le contrôle de sélecteur a trouvé la cause racine de l'anomalie
`n8n-postgres-ro`, **sans aucun appel au modèle** :

```
! sélecteur du service : aucun pod ne porte
  « cnpg.io/cluster=n8n-postgres,cnpg.io/instanceRole=replica »
  — cause probable du service sans endpoint
```

C'est un cluster CloudNativePG configuré sans réplica : le service de lecture
seule ne peut router vers rien. Diagnostic complet, coût nul.

## Garantie

Un test vérifie que le module n'importe **jamais** de client LLM — la
propriété « zéro token » est ainsi protégée contre une régression future.
