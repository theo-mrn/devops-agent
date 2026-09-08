# Installation sur un cluster

## Publication de l'image

Par la CI, ce qui est la voie normale :

```bash
git tag v0.1.0 && git push origin v0.1.0
```

GitHub Actions construit en `linux/amd64`, publie sur GHCR et attache une
attestation de provenance. Le `GITHUB_TOKEN` porte déjà `write:packages` :
aucun jeton à créer.

`deploy/publier.sh` reste disponible pour une publication depuis un poste,
mais demande d'ajouter la portée `write:packages` au jeton `gh`.

### Rendre l'image publique

Sur GHCR, une image est **privée par défaut**, même dans un dépôt public. Sans
cela, le cluster échoue au `pull` :

> https://github.com/users/&lt;compte&gt;/packages/container/devops-agent/settings
> → *Change visibility* → *Public*

Sinon, créer un `imagePullSecret` :

```bash
kubectl create secret docker-registry ghcr \
  --docker-server=ghcr.io --docker-username=<compte> \
  --docker-password=$(gh auth token) -n devops-agent
```

## Installation

```bash
./deploy/installer.sh                     # RBAC, vérification, déploiement
kubectl logs -f deploy/devops-agent -n devops-agent
```

## Ce que fait `installer.sh`

1. **Applique le RBAC** — namespace, ServiceAccount, ClusterRole.
2. **Vérifie les permissions** — et c'est le cœur du script.
3. **Crée le secret** de la clé API, depuis `ANTHROPIC_API_KEY` ou `.env`.
4. **Déploie** l'agent.

L'étape 2 interroge le serveur d'API sur ce que le ServiceAccount peut
réellement faire, plutôt que de faire confiance au YAML :

```
✓ get pods                    yes
✓ watch pods                  yes
✓ get pods/log                yes
✓ delete pods                 no
✓ get secrets                 no
✓ create pods/exec            no
```

**Si une seule permission est incorrecte, l'installation s'interrompt.** Un
RBAC appliqué n'est pas un RBAC vérifié.

## Vérifier à tout moment

```bash
devops-agent audit --sa devops-agent:devops-agent
```

Sans `--sa`, la commande audite l'identité courante — souvent un kubeconfig
administrateur, ce qui ne dit rien des permissions de l'agent.

## Architecture d'un point de vue sûreté

```
┌─────────────────────────────────────────┐
│  Code Python                            │
│  liste blanche de verbes, 20 tests      │  ← première couche
└─────────────────────────────────────────┘
                  ↓
┌─────────────────────────────────────────┐
│  ServiceAccount + ClusterRole           │
│  le serveur d'API refuse l'écriture     │  ← seconde couche
└─────────────────────────────────────────┘
```

La seconde tient même si la première est contournée. C'est elle qu'une équipe
infra lira avant d'autoriser le déploiement.

## Points d'attention

**Architecture de l'image.** Un Mac construit en `arm64`, les nœuds sont en
général en `amd64`. `publier.sh` force `linux/amd64` — sans quoi le pod
resterait en `CrashLoopBackOff` avec `exec format error`.

**Portée du jeton GitHub.** Publier sur GHCR demande `write:packages`, que le
jeton `gh` n a pas par défaut :

```bash
gh auth refresh --scopes write:packages,read:packages
gh auth token | docker login ghcr.io -u <compte> --password-stdin
```

`publier.sh` le vérifie avant de construire — un `denied` après trois minutes
de build est une perte de temps.

**Image privée sur GHCR.** Par défaut, une image publiée sur GHCR est privée.
Soit la rendre publique dans les réglages du package, soit créer un
`imagePullSecret` — `publier.sh` affiche les deux commandes.

**Le Secret n'est pas dans le manifest.** Un placeholder appliqué par mégarde
écraserait la vraie clé. `installer.sh` le crée séparément.

**Une seule réplique.** Deux agents diagnostiqueraient deux fois le même
incident et doubleraient la facture.

## Première mise en service — ce qui s'est passé

Installation sur un k3s à 3 nœuds, 57 pods :

```
1. RBAC appliqué
2. 18 permissions vérifiées — 10 lectures autorisées, 8 écritures refusées
3. Secret créé depuis .env
4. Agent déployé — Running en 17 s
```

L'agent a détecté ses deux anomalies au démarrage, puis diagnostiqué la
première en autonomie :

```
4 tours · 6 appels d'outil · 32,5 s
8 tokens frais + 12 830 en cache + 2 723 générés
100 % depuis le cache · 0,0498 $
```

Le cache d'historique fonctionne comme prévu : **la totalité du contexte
répété est lue à 10 % du prix**.

### Le RBAC a joué son rôle

Le diagnostic mentionne explicitement :

> Accès direct à la ressource `Cluster n8n-postgres` refusé (RBAC), donc
> impossible de confirmer via le CR le nombre d'instances déclaré.

L'agent a été bloqué sur une ressource non autorisée — les CRD de
CloudNativePG — et l'a **signalé** au lieu d'inventer. C'est le comportement
attendu.

### Limite constatée

Les CRD d'opérateurs (CloudNativePG, cert-manager, Argo CD…) ne sont pas dans
le ClusterRole. Ce n'est pas un défaut de sûreté — ce sont des lectures — mais
ça limite le diagnostic sur les charges gérées par opérateur.

Les ajouter demande de nommer chaque groupe d'API explicitement, ce qui reste
compatible avec la lecture seule stricte.

### Bug corrigé dans installer.sh

`kubectl auth can-i` écrit « no » sur la sortie standard **et** sort avec le
code 1. Le `|| echo "no"` du script ajoutait donc un second « no », et la
comparaison échouait sur les huit permissions correctement refusées —
l'installation s'interrompait alors que le RBAC était parfait.
