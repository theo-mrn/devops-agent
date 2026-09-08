# Workflows n8n

Deux flux séparés, un par source d'information :

| fichier | source | ce qu'il apporte |
|---|---|---|
| `devops-agent-minio-discord.json` | l'agent | diagnostics archivés, gravité ≥ grave |
| `devops-agent-surveillance.json` | l'agent | bruit de fond, Discord seul |
| `alertmanager-minio-discord.json` | Prometheus | alertes métriques et de tendance |

## Pourquoi deux flux

L'agent et Alertmanager ne voient pas la même chose :

| | l'agent | Alertmanager |
|---|---|---|
| pods, services, PVC, nœuds | ✅ | ✅ |
| métriques (CPU, latence, taux d'erreur) | ❌ | ✅ |
| règles métier (SLO, seuils applicatifs) | ❌ | ✅ |
| certificats expirants, disque à 85 % | ❌ | ✅ |
| **diagnostic de la cause** | ✅ | ❌ |
| **corrélation d'anomalies liées** | ✅ | ❌ |

L'agent observe l'état des objets ; Prometheus observe les tendances. Une
latence qui monte ne produit aucune anomalie d'objet.

**k-AI a été retiré de la boucle.** Il investiguait les alertes Alertmanager ;
l'agent fait ce travail de lui-même, sans attendre qu'une alerte se déclenche.
Les alertes métriques sont désormais relayées telles quelles.

---

## Flux de l'agent

`devops-agent-minio-discord.json`

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


---

# Flux Alertmanager

`alertmanager-minio-discord.json`

```
Alertmanager → n8n → MinIO + Discord
```

Aucune investigation : les alertes métriques sont relayées telles quelles.
Le diagnostic d'objets Kubernetes est le rôle de l'agent, sur son propre flux.

## Ce que fait le nœud de mise en forme

- **ignore les alertes résolues** — Alertmanager notifie aussi les extinctions,
  qui n'ont pas à produire de rapport ;
- **groupe les alertes** arrivées ensemble dans un seul rapport ;
- **filtre les labels** internes de Prometheus (`job`, `instance`, `endpoint`…)
  pour ne garder que le contexte utile.

## Archivage séparé

```
kai-reports/
├── 2026/09/08/…              diagnostics de l'agent
└── alertes/2026/09/08/…      alertes Prometheus
```

Le préfixe `alertes/` distingue les deux sources dans le même bucket.

## Configuration d'Alertmanager

```yaml
receivers:
  - name: n8n
    webhook_configs:
      - url: http://n8n.n8n.svc.cluster.local:5678/webhook/alertmanager
        send_resolved: false   # les résolutions sont filtrées côté n8n
```


---

# Flux de surveillance

`devops-agent-surveillance.json`

```
agent (gravité surveillance) → n8n → Discord
```

**Pas d'archivage.** Ces événements sont du bruit de fond — jobs éphémères,
replicas temporairement incomplets, dérives mémoire encore sous contrôle. Ils
méritent d'être vus, pas conservés.

## Message compact

Seule la section « Diagnostic » du rapport est reprise : sur un canal de
surveillance, les constats et le correctif détaillé sont du bruit.

```
🔵 pod scan-vuln-abc
Le job de scan a échoué faute de mémoire.
Namespace trivy-system · Coût 0.012 $
```

## Installation

1. Créer dans n8n un identifiant Discord pointant vers le salon de
   surveillance — **distinct** de celui des incidents.
2. Importer le workflow et lui associer cet identifiant.
3. Router la gravité `surveillance` vers ce webhook :

```bash
kubectl set env deployment/devops-agent -n devops-agent \
  RAG_WEBHOOK_URL_SURVEILLANCE=http://n8n.n8n.svc.cluster.local:5678/webhook/devops-agent-surveillance
```

Le seuil `RAG_WEBHOOK_GRAVITE=grave` continue de filtrer la destination
générique ; une gravité dotée de sa propre URL est toujours transmise.

## Le jeton Discord

Il vit dans les **credentials n8n**, jamais dans ce dépôt ni dans un manifest
Kubernetes. Un jeton de webhook Discord donne le droit de publier dans le
salon : le committer reviendrait à l'offrir.
