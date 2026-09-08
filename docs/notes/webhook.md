# Envoi des rapports

## Le manque

Un diagnostic vivait dans les logs du pod et dans `/data/diagnostics.jsonl`.
Pour le lire : `kubectl exec`. Inutilisable au quotidien — un rapport que
personne ne lit ne sert à rien.

## Webhook générique

```bash
RAG_WEBHOOK_URL=https://n8n.exemple/webhook/diagnostics
```

Un POST JSON à chaque diagnostic. Derrière, n'importe quoi qui reçoit du
HTTP : n8n, Slack, Discord, Teams, un service maison. Une seule
implémentation plutôt qu'un connecteur par plateforme.

## Charge utile

```json
{
  "horodatage": "2026-09-08T08:07:15+0000",
  "cluster": "prod-eu-west",
  "gravite": "critique",
  "namespace": "sonarqube",
  "anomalies": [
    {"ressource": "pod", "nom": "sonarqube-0",
     "etat": "OOMKilled", "redemarrages": 1}
  ],
  "resume": "[pod] sonarqube/sonarqube-0  OOMKilled (1 redémarrages)",
  "diagnostic": "## Diagnostic\n…",
  "verifications": ["✓ limite mémoire : 2Gi"],
  "outils_appeles": ["kubectl", "ressources_pod"],
  "cout_dollars": 0.0498
}
```

## Trois exigences respectées

**Le rapport est assaini avant l'envoi.** Il cite des extraits de logs et de
manifests : un `DB_PASSWORD=hunter2` ne doit pas sortir du cluster. Vérifié
par test.

**Un échec n'interrompt jamais la surveillance.** Le webhook injoignable est
signalé, le diagnostic reste dans le journal local, l'agent continue.

**Les erreurs 4xx ne sont pas réessayées.** Une 404 sur l'URL ne se corrigera
pas en insistant ; une 503 si. Trois tentatives avec attente croissante pour
les secondes.

## Réglages

| variable | rôle | défaut |
|---|---|---|
| `RAG_WEBHOOK_URL` | destination ; vide = désactivé | — |
| `RAG_WEBHOOK_AUTH` | en-tête `Authorization` | — |
| `RAG_WEBHOOK_GRAVITE` | seuil : `surveillance`, `grave`, `critique` | `surveillance` |
| `RAG_WEBHOOK_TIMEOUT` | secondes | 15 |
| `RAG_CLUSTER_NOM` | étiquette reprise dans chaque rapport | — |

Le filtrage par gravité évite d'inonder un canal : `critique` ne transmet que
ce qui exige une action immédiate.

## Sans dépendance

Écrit avec `urllib`, pas `requests` : une dépendance de moins dans une image
qu'on veut légère.
