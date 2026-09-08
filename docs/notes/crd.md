# RBAC pour un outil distribuable

## Le problème de l'énumération

Première approche : nommer chaque groupe d'API — `postgresql.cnpg.io`,
`argoproj.io`, `traefik.io`… Huit groupes, choisis d'après les CRD présentes
sur un cluster donné.

**Ça ne tient pas pour un outil distribué.** Chaque client a ses opérateurs, et
personne n'éditera un ClusterRole avant d'installer. Un agent qui ne peut pas
lire la ressource `Cluster` d'un opérateur PostgreSQL diagnostique à l'aveugle.

## La règle retenue

```yaml
- apiGroups: ["*"]
  resources: ["*"]
  verbs: ["get", "list", "watch"]
```

Une seule règle. L'agent lit tout ce qui existe — présent et futur, quels que
soient les opérateurs installés — et ne peut **rien** écrire.

**Le joker porte sur les ressources, jamais sur les verbes.** C'est ce qui rend
le compromis acceptable : la sûreté ne dépend pas d'une liste à maintenir.

## Le contrat en deux couches

```
RBAC          empêche toute ÉCRITURE
              (serveur d'API, incontournable)

code          empêche la LECTURE de ce qui ne doit pas sortir
              (RESSOURCES_INTERDITES, couvert par des tests)
```

Kubernetes ne sait pas exclure une ressource d'un joker : le joker inclut donc
les Secrets. Leur blocage vit dans `agent/tools.py` :

| bloqué | raison |
|---|---|
| `secrets` | encodés en base64, donc lisibles |
| `serviceaccounttokens` | jetons d'identité |
| `certificaterequests` | clés privées de cert-manager |
| `secretstores`, `clustersecretstores` | identifiants de fournisseurs externes |
| `vaultauth`, `vaultconnection` | accès Vault |

**Non bloqués, délibérément** : `sealedsecrets` et `externalsecrets`. Leur
contenu est chiffré ou n'est qu'une référence, et savoir qu'ils existent aide
au diagnostic.

## Vérification sur le cluster

```
get  pods                             yes     ← lecture
get  clusters.postgresql.cnpg.io      yes
get  ingressroutes.traefik.io         yes
get  helmcharts.helm.cattle.io        yes

delete pods                           no      ← écriture
create pods/exec                      no
patch  clusters.postgresql.cnpg.io    no
create clusterrolebindings            no
```

Les sous-ressources d'exécution — `pods/exec`, `pods/portforward`,
`pods/attach` — exigent le verbe `create`, absent du rôle. Elles sont donc
inaccessibles sans avoir à être nommées.

## Deux variantes livrées

```bash
kubectl apply -f deploy/rbac.yaml          # joker — installation immédiate
kubectl apply -f deploy/rbac-strict.yaml   # énumération — secrets bloqués par l'API
```

Les deux créent le **même** ClusterRole : elles sont interchangeables, un
`kubectl apply` de l'autre suffit à basculer.

| | `rbac.yaml` | `rbac-strict.yaml` |
|---|---|---|
| secrets bloqués par | le code | **le serveur d'API** |
| opérateur inconnu | lisible | invisible |
| maintenance | aucune | à étendre par opérateur |
| lignes | 3 (une règle) | ~120 |

### Ce que la variante stricte coûte

Un opérateur absent de la liste devient invisible pour l'agent. Vérifié sur le
cluster :

```
get secrets                              no    ← l'objectif
get certificaterequests.cert-manager.io  no    ← clés privées
get pods                                 yes
get clusters.postgresql.cnpg.io          yes
get sealedsecrets.bitnami.com            no    ← groupe non listé
```

Le dernier cas illustre le compromis : `bitnami.com` n'est pas dans la liste,
donc les SealedSecrets deviennent invisibles. Le diagnostic est **dégradé** sur
ces objets, jamais en panne — l'agent signale simplement qu'il n'y a pas accès.

Étendre la liste demande de connaître les groupes présents :

```bash
kubectl get crd -o jsonpath='{range .items[*]}{.spec.group}{"\n"}{end}' | sort -u
```

### Pourquoi le RBAC ne sait pas faire « tout sauf »

Il est **purement additif** : on ne peut qu'accorder, jamais retrancher. L'API
le confirme si l'on essaie une règle à verbes vides :

```
The ClusterRole is invalid: rules[1].verbs: Required value:
verbs must contain at least one value
```

Il n'existe ni `deny`, ni `except`. La seule façon d'interdire une ressource
est de ne jamais l'accorder — donc d'énumérer tout le reste.

## Tests qui garantissent le contrat

| test | vérifie |
|---|---|
| `test_seuls_des_verbes_de_lecture` | aucun verbe hors `get`/`list`/`watch` |
| `test_execution_inaccessible` | `create` absent, donc `pods/exec` bloqué |
| `test_secrets_bloques_par_le_code` | le code refuse ce que le RBAC autorise |
| `test_ressources_sensibles_bloquees` | CRD portant des identifiants |
| `test_ressources_chiffrees_lisibles` | un SealedSecret reste lisible |
