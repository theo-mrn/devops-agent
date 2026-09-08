# Installation sur un cluster

## En trois commandes

```bash
./deploy/publier.sh                       # construit et publie l'image
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

**Image privée sur GHCR.** Par défaut, une image publiée sur GHCR est privée.
Soit la rendre publique dans les réglages du package, soit créer un
`imagePullSecret` — `publier.sh` affiche les deux commandes.

**Le Secret n'est pas dans le manifest.** Un placeholder appliqué par mégarde
écraserait la vraie clé. `installer.sh` le crée séparément.

**Une seule réplique.** Deux agents diagnostiqueraient deux fois le même
incident et doubleraient la facture.
