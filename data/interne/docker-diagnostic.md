---
title: Diagnostic de conteneurs Docker
description: États, codes de sortie et procédures d'investigation pour les conteneurs Docker.
---

# Diagnostic de conteneurs Docker

## États d'un conteneur

| État | Signification |
| --- | --- |
| `created` | Créé mais jamais démarré |
| `running` | En cours d'exécution |
| `restarting` | En redémarrage, selon la politique `--restart` |
| `exited` | Terminé — le code de sortie indique la cause |
| `paused` | Suspendu par `docker pause` |
| `dead` | Le daemon n'a pas pu le supprimer proprement |

```bash
docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
docker inspect <conteneur> --format '{{.State.Status}} {{.State.ExitCode}}'
```

## Codes de sortie

La convention est identique à Kubernetes, puisque les deux reposent sur les
signaux Linux : un code supérieur à 128 vaut `128 + numéro du signal`.

| Code | Cause |
| --- | --- |
| 0 | Arrêt normal |
| 1 | Erreur applicative |
| 125 | Le daemon Docker lui-même a échoué (option invalide) |
| 126 | Commande trouvée mais non exécutable |
| 127 | Commande introuvable dans l'image |
| 137 | **SIGKILL — OOM ou `docker kill`** |
| 139 | SIGSEGV |
| 143 | SIGTERM — `docker stop` |

### Exit 137 : distinguer OOM et arrêt manuel

```bash
docker inspect <conteneur> --format '{{.State.OOMKilled}}'
```

Le champ `OOMKilled` vaut `true` si le conteneur a dépassé sa limite mémoire,
`false` s'il a été tué autrement (`docker kill`, arrêt du daemon).

Vérifier la limite configurée :

```bash
docker inspect <conteneur> --format '{{.HostConfig.Memory}}'
docker stats --no-stream <conteneur>
```

Une valeur `0` pour `Memory` signifie aucune limite : le conteneur peut alors
épuiser la mémoire de l'hôte et déclencher le OOM killer du noyau.

## Investigation

### Logs

```bash
docker logs <conteneur> --tail 100
docker logs <conteneur> --since 10m
docker logs <conteneur> -f
```

Contrairement à Kubernetes, Docker conserve les logs du conteneur arrêté : il
n'existe pas d'équivalent de `--previous`, les logs restent accessibles tant que
le conteneur n'est pas supprimé.

### Inspection

```bash
docker inspect <conteneur>
docker inspect <conteneur> --format '{{json .State}}' | jq
docker top <conteneur>
docker diff <conteneur>          # fichiers modifiés depuis le démarrage
```

### Entrer dans un conteneur

```bash
docker exec -it <conteneur> sh
docker exec <conteneur> ps aux
```

Sur une image `distroless` ou `scratch`, aucun shell n'existe. Utiliser alors un
conteneur de débogage partageant les namespaces :

```bash
docker run -it --rm --pid=container:<conteneur> --network=container:<conteneur> nicolaka/netshoot
```

## Problèmes fréquents

### Le conteneur redémarre en boucle

```bash
docker inspect <conteneur> --format '{{.RestartCount}} {{.HostConfig.RestartPolicy.Name}}'
docker logs <conteneur> --tail 50
```

Une politique `always` masque un processus qui échoue immédiatement. Vérifier
que le processus principal reste au premier plan : un conteneur dont le PID 1
se termine s'arrête, même si des démons tournent en arrière-plan.

### Image introuvable

```bash
docker pull <image>
docker image inspect <image>
```

Un `manifest unknown` signale un tag inexistant ; un `unauthorized` un défaut
d'authentification (`docker login`).

### Port déjà utilisé

```bash
ss -tlnp | grep <port>
docker ps --filter "publish=<port>"
```

### Espace disque saturé

```bash
docker system df
docker system df -v
```

Les images et volumes orphelins s'accumulent. Le nettoyage :

```bash
docker image prune           # images sans tag
docker container prune       # conteneurs arrêtés
docker system prune -a       # TOUT ce qui n'est pas utilisé — vérifier avant
```

`docker system prune -a` supprime toutes les images non utilisées par un
conteneur en cours. Sur un hôte de build, cela peut effacer des images qu'il
faudra retélécharger. Toujours inspecter `docker system df -v` d'abord.

### Réseau : conteneurs qui ne se voient pas

```bash
docker network ls
docker network inspect <réseau>
docker exec <conteneur> getent hosts <autre-conteneur>
```

Sur le réseau `bridge` par défaut, la résolution par nom ne fonctionne pas. Il
faut un réseau utilisateur créé avec `docker network create`.

## Ordre d'investigation

1. **État et code** — `docker ps -a`, `docker inspect --format '{{.State.ExitCode}}'`
2. **OOM ?** — `docker inspect --format '{{.State.OOMKilled}}'`
3. **Logs** — `docker logs --tail 100`
4. **Ressources** — `docker stats --no-stream`, `docker system df`
5. **Agir** — `docker restart` avant `docker rm`, et jamais `prune -a` sans vérification
