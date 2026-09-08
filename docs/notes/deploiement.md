# Déploiement en cluster

## Pourquoi le RBAC compte

La lecture seule est aujourd'hui garantie par du code Python : une liste
blanche de verbes `kubectl`, couverte par 20 tests. C'est solide, mais ça
repose sur l'absence de faille dans le code.

Avec un ServiceAccount en RBAC `get`/`list`/`watch`, **le serveur d'API refuse
l'écriture** — quelle que soit la commande émise, même si le code était
contourné.

Pour une distribution en entreprise, c'est l'argument décisif : une équipe
infra ne relira pas le code Python, mais elle lira le `ClusterRole` en trente
secondes.

## Ce que l'audit a révélé

```
devops-agent audit
```

Sur un kubeconfig d'administrateur :

```
✓ get pods, get services, get nodes…    autorisé
! delete pods                           autorisé
! create secrets                        autorisé
! get secrets                           autorisé
! create pods/exec                      autorisé

8 permission(s) inattendue(s) — appliquer deploy/rbac.yaml
```

Avec le ServiceAccount, ces huit lignes passent au vert.

## Délibérément absents du ClusterRole

| absent | raison |
|---|---|
| `secrets` | contenu encodé en base64, donc lisible |
| `create`/`update`/`patch`/`delete` | aucun verbe d'écriture |
| `pods/exec`, `pods/portforward` | aucune exécution dans un conteneur |
| `*/scale` | aucune modification de dimensionnement |

## Dépendances RAG rendues optionnelles

La première image embarquait PyTorch et `sentence-transformers` : **~3 Go et
plus de dix minutes de construction**, pour un outil sur cinq.

Séparation :

```bash
uv sync                  # agent seul  — image de 320 Mo
uv sync --extra rag      # + recherche documentaire
```

Sans les dépendances RAG, `chercher_documentation` renvoie un message
explicite indiquant comment les installer, et les quatre autres outils
fonctionnent normalement.

**Image finale : 320 Mo**, construite en ~2 minutes.

## Durcissement du conteneur

- utilisateur non privilégié (uid 10001), vérifié : `uid=10001(agent)`
- `readOnlyRootFilesystem: true`, avec des `emptyDir` pour `/tmp` et le cache
- `allowPrivilegeEscalation: false`, toutes les capabilities retirées
- une seule réplique — deux agents diagnostiqueraient deux fois le même
  incident et doubleraient la facture

## Installation

```bash
kubectl apply -f deploy/rbac.yaml
kubectl create secret generic devops-agent-api \
  --from-literal=anthropic-api-key=sk-ant-... -n devops-agent
kubectl apply -f deploy/agent.yaml
```

L'agent détecte seul qu'il tourne dans le cluster : la présence du jeton de
ServiceAccount suffit, aucun kubeconfig n'est nécessaire.

## Un test qui a trouvé un vrai écart

`test_detecteurs_et_rbac_concordent` compare ce que l'agent surveille à ce que
le RBAC autorise. Il a signalé `pvc` comme manquant — c'est l'abréviation
kubectl, le RBAC exige `persistentvolumeclaims`. Le test connaît désormais
les alias, et il garantit qu'ajouter un détecteur sans la permission
correspondante fera échouer la suite.
