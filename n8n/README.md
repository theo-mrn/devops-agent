# Workflow n8n

`devops-agent-minio-discord.json` — à importer dans n8n.

## Différence avec le flux k-AI

```
k-AI          Alertmanager → n8n → POST /investigate → attente → MinIO → Discord
devops-agent  agent (diagnostique seul) → n8n → MinIO → Discord
```

L'agent surveille et diagnostique de lui-même : n8n ne fait plus que recevoir,
archiver et notifier. Disparaissent le nœud `Has Alert?`, l'appel HTTP sortant
et l'attente pouvant aller jusqu'à cinq minutes.

## Nœuds

| nœud | rôle |
|---|---|
| Webhook | reçoit le POST de l'agent sur `/webhook/devops-agent` |
| Répondre immédiatement | acquitte sans attendre le reste |
| Mise en forme du rapport | embed Discord + Markdown + HTML |
| MinIO — Markdown / HTML | archivage dans `kai-reports` |
| Discord | notification, titre pointant vers le rapport HTML |

## Installation

1. Importer le JSON dans n8n.
2. Vérifier les identifiants S3 et Discord (repris du flux k-AI).
3. Activer le workflow, relever l'URL du webhook.
4. La déclarer sur l'agent :

```bash
kubectl set env deployment/devops-agent -n devops-agent \
  RAG_WEBHOOK_URL=http://n8n.n8n.svc.cluster.local:5678/webhook/devops-agent \
  RAG_CLUSTER_NOM=prod-k3s
```

L'URL interne évite de sortir du cluster pour y revenir.

## Charge reçue

```json
{
  "horodatage": "2026-09-08T08:07:15+0000",
  "cluster": "prod-k3s",
  "gravite": "critique",
  "namespace": "sonarqube",
  "anomalies": [
    {"ressource": "pod", "nom": "sonarqube-0",
     "etat": "OOMKilled", "redemarrages": 1}
  ],
  "resume": "…",
  "diagnostic": "## Diagnostic\n…",
  "verifications": ["✓ limite mémoire : 2Gi"],
  "outils_appeles": ["kubectl", "ressources_pod"],
  "cout_dollars": 0.0498
}
```

Le champ `diagnostic` est du Markdown complet — diagnostic, constats,
correctif exécutable, vérification. Le `verifications` vient du contrôle
mécanique, qui confronte les affirmations du rapport à l'état réel du cluster.

## Arborescence MinIO

```
2026/09/08/10h16m48s-sonarqube-sonarqube-0/
├── rapport.md      navigation dans la console MinIO
└── rapport.html    lien du titre Discord, s'affiche dans le navigateur
```

Un dossier par incident : les deux formats restent groupés et la navigation
par date reste lisible.

## Anomalies corrélées

L'agent regroupe les anomalies liées : un PVC bloqué, le pod qui l'attend et
le service sans endpoint forment **un seul rapport**.

```
🔴 3 anomalies liées dans prod
  • `pvc/postgres-data` — VolumeNonLie
  • `pod/postgres-0` — Pending
  • `service/postgres` — SansEndpoint
```

Le flux k-AI recevait trois alertes Alertmanager distinctes et produisait trois
rapports.

## Filtrer le volume

```bash
RAG_WEBHOOK_GRAVITE=critique   # ou grave / surveillance (défaut)
```

`critique` ne transmet que ce qui exige une action immédiate.
