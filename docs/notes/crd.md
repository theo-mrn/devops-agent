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

## Pour un déploiement plus strict

Une organisation qui exige que les Secrets soient inaccessibles **au niveau du
serveur d'API** peut remplacer le joker par une énumération explicite. Elle
accepte alors de la maintenir à chaque nouvel opérateur.

Le compromis inverse — joker plus filtrage applicatif — est celui qui rend
l'agent installable en une commande.

## Tests qui garantissent le contrat

| test | vérifie |
|---|---|
| `test_seuls_des_verbes_de_lecture` | aucun verbe hors `get`/`list`/`watch` |
| `test_execution_inaccessible` | `create` absent, donc `pods/exec` bloqué |
| `test_secrets_bloques_par_le_code` | le code refuse ce que le RBAC autorise |
| `test_ressources_sensibles_bloquees` | CRD portant des identifiants |
| `test_ressources_chiffrees_lisibles` | un SealedSecret reste lisible |
