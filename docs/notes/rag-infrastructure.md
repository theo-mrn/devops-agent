# RAG sur une infrastructure réelle

## Le moment où le RAG devient utile

Sur de la documentation publique, le RAG apporte peu à un agent qui tourne sur
Claude : le modèle connaît Kubernetes mieux que l'index.

Il devient irremplaçable sur ce qu'aucun modèle ne peut savoir — **cette
infrastructure-ci**.

## Deux sources

```bash
RAG_INFRA_REPO=/chemin/vers/le/depot-gitops
```

| source | contenu | apporte |
|---|---|---|
| `infra` | les manifests YAML | l'intention déclarée en Git |
| `runbook` | `docs/**/*.md` | le pourquoi, les incidents, les procédures |

Sur le dépôt de test : **61 ressources et 26 sections**, pour un corpus total
de 2076 chunks.

## Chunking adapté au YAML

Le découpage par titre markdown n'a pas de sens ici. La règle retenue :
**un chunk = une ressource Kubernetes**.

```
[Cluster n8n/n8n-postgres · manifest kubernetes/system/n8n/db-cluster.yml]
# Intention : Base de données de l'application, volontairement mono-instance
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
spec:
  instances: 1
  …
```

Trois choix :

**L'en-tête situe la ressource.** Sans lui, un `spec:` isolé ne dit ni de quoi
il parle ni où il vit.

**Les commentaires sont extraits.** Ils portent l'intention — pourquoi cette
limite, pourquoi cette exception — que `kubectl get -o yaml` ne montre jamais.

**Le statut et les métadonnées générées sont retirés.** `creationTimestamp`,
`uid`, `resourceVersion`, `status` polluent l'embedding : le statut d'un
manifest Git est de toute façon périmé, c'est `kubectl` qui fait foi.

Les `Secret` et `SealedSecret` ne sont pas indexés — du chiffré noierait
l'index sous des chaînes aléatoires.

## Résultat mesuré

```
« Pourquoi le service n8n-postgres-ro n'a pas d'endpoint ? »
  0.801  [runbook] decisions/postgres-mono-instance.md — Conséquence attendue

« Quelle limite mémoire pour sonarqube ? »
  0.974  [runbook] incidents/2026-09-sonarqube-oomkilled.md — Cause
  0.905  [runbook] runbooks/pod-oomkilled.md — Cas particulier des JVM
```

La première question avait coûté **4 tours et 0,04 $** à l'agent, qui avait dû
la déduire de l'absence de pods réplicas. Elle se répond désormais en une
recherche.

## Le prompt a changé aussi

Une règle ajoutée à la boucle :

> AVANT de conclure à une anomalie, cherche si elle est documentée : un
> service sans endpoint peut être une conséquence assumée d'un choix de
> configuration, pas une panne.

Sans cela, l'agent rediagnostiquerait indéfiniment un comportement voulu.

## Manifests ou kubectl ?

L'agent a les deux, et ils ne disent pas la même chose :

| | `kubectl` | manifests indexés |
|---|---|---|
| état réel | ✅ | ❌ |
| intention déclarée | ❌ | ✅ |
| recherche floue | ❌ | ✅ |
| commentaires, pourquoi | ❌ | ✅ |
| dérive Git ↔ cluster | | comparaison possible |

## Ce qui reste à écrire

La structure est en place :

```
docs/
├── runbooks/    procédures maison
├── decisions/   choix d'architecture et leur raison
└── incidents/   ce qui a déjà cassé
```

Trois documents seulement, tirés des diagnostics réels de l'agent. C'est peu,
mais ce sont déjà les mieux classés du corpus — parce qu'ils répondent à des
questions que rien d'autre ne couvre.

Chaque incident diagnostiqué mérite d'y laisser une trace : c'est ce qui
transforme un outil de diagnostic en mémoire d'équipe.
