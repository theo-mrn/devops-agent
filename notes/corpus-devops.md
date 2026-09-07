# Brique 12 — Élargissement au métier DevOps

L'assistant couvrait **deux outils** (Kubernetes, Terraform), pas le métier.
Ajout de Docker, Linux et CI/CD.

## Corpus

| | avant | après |
|---|---:|---:|
| fichiers | 174 | **243** |
| chunks | 1427 | **1989** |
| index | 7,3 Mo | **10,2 Mo** |
| encodage | 109 s | 146 s |

## Sources ajoutées

**Docker** (39 fichiers, `docker/docs`) — containers, daemon, logging,
manage-resources, network, storage.

**CI/CD** (38 fichiers, `github/docs`) — concepts, reference, manage-workflow-runs.

**Linux** — ❌ **rien de récupérable.** La doc `systemd/systemd` est de la
documentation de *contributeur* (`CODE_OF_CONDUCT.md`, `BACKPORTS.md`,
`BUILDING_IMAGES.md`), pas du diagnostic opérationnel. L'indexer aurait ajouté
du bruit sans valeur.

## Deux documents internes de plus

Même méthode qu'en brique 9, celle qui avait résolu le cas OOMKilled : **rédiger
ce qui n'existe nulle part**.

- `data/interne/linux-diagnostic.md` — services systemd (codes `status=203/EXEC`,
  `daemon-reload`), ressources (`available` vs `free`, inodes), réseau (`ss`),
  journaux, processus en état `D`, signaux et convention `128 + signal`.
- `data/interne/docker-diagnostic.md` — états, codes de sortie, distinction
  OOM / `docker kill` via `.State.OOMKilled`, débogage d'image distroless,
  réserves sur `system prune -a`.

Les documents internes pèsent maintenant **23 chunks sur 1989** — 1,2 % du
corpus, et ce sont ceux qui remontent en tête sur les questions de diagnostic.

## Vérification immédiate

```
Q: exit code 137 sur un conteneur Docker, comment savoir si c'est un OOM ?
   0.985  interne/docker-diagnostic — Codes de sortie
   0.979  interne/exit-codes — Exit code 137 : OOMKilled
   → docker inspect <conteneur> --format '{{.State.OOMKilled}}'

Q: pourquoi un service systemd refuse de démarrer ?
   0.947  interne/linux-diagnostic — Services systemd
   0.048  docker-daemon/troubleshoot        ← écart de 0,9
   → systemctl status + journalctl -u
```

Le reranker sépare nettement : 0,947 contre 0,048 pour le suivant.

## Jeu d'évaluation

**93 → 115 cas.** Ajout de 7 cas Docker, 9 Linux, 5 CI/CD, 1 absent.

Les cas Linux visent surtout des **pièges de terrain** :
- `free -h` : c'est `available` qui compte, pas `free`
- « No space left » avec du disque libre → épuisement des **inodes**
- oubli du `daemon-reload` après modification d'une unité
- processus en état `D` : non tuable même par SIGKILL
