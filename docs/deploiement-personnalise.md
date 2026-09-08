# Adapter le déploiement à son installation

Le dépôt ne contient volontairement **aucune information propre à un
cluster** : ni domaine, ni identifiant de credentials, ni topologie interne.
Tout ce qui varie se configure au déploiement.

## Ce qui n'est pas dans le dépôt

| élément | où il vit |
|---|---|
| clé API Anthropic | Secret Kubernetes, créé par `installer.sh` |
| URL des webhooks | ConfigMap ou `kubectl set env` |
| workflows n8n | l'instance n8n ; le dossier `n8n/` est ignoré par git |
| jetons Discord, identifiants S3 | credentials n8n |
| domaines de rapports | variables n8n (`$vars.RAPPORTS_URL`) |

## Configurer les destinations

```bash
kubectl set env deployment/devops-agent -n devops-agent \
  RAG_WEBHOOK_URL_CRITIQUE=http://<n8n>.<ns>.svc.cluster.local:5678/webhook/urgences \
  RAG_WEBHOOK_URL_SURVEILLANCE=http://<n8n>.<ns>.svc.cluster.local:5678/webhook/journal \
  RAG_CLUSTER_NOM=<nom-du-cluster>
```

Ces valeurs vivent dans le Deployment, pas dans git.

## Publier sous son propre compte

```bash
COMPTE=mon-compte ./deploy/publier.sh
```

Puis pointer le manifest vers l'image publiée :

```bash
kubectl set image deployment/devops-agent \
  agent=mon-compte/devops-agent:0.4.0 -n devops-agent
```

## Workflows n8n

Ils ne sont pas versionnés : chaque installation a ses domaines, ses
credentials et ses salons. Trois flux à créer côté n8n :

| flux | reçoit | fait |
|---|---|---|
| incidents | gravité ≥ grave | archive puis notifie |
| surveillance | gravité `surveillance` | notifie seulement |
| alertes | Alertmanager | archive puis notifie |

L'agent envoie une charge JSON documentée dans `docs/notes/webhook.md` ; la
mise en forme appartient à l'orchestrateur.
