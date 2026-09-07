---
title: Diagnostic système Linux pour SRE
description: Commandes et procédures d'investigation sur un serveur Linux — services, ressources, réseau, logs.
---

# Diagnostic système Linux

## Services systemd

### État d'un service

```bash
systemctl status <service>
systemctl is-active <service>
systemctl is-enabled <service>
```

La sortie de `status` donne l'état courant, le PID principal, la consommation
mémoire et les dernières lignes de journal. Le champ `Active:` indique
`active (running)`, `failed`, `inactive (dead)` ou `activating`.

### Un service qui refuse de démarrer

```bash
systemctl status <service> --no-pager -l
journalctl -u <service> -n 100 --no-pager
journalctl -u <service> --since "10 min ago"
```

Le code de sortie apparaît dans `Main PID: 1234 (code=exited, status=1/FAILURE)`.
Un `status=203/EXEC` signale que le binaire est introuvable ou non exécutable ;
`status=209/STDOUT` un problème de redirection ; `status=200/CHDIR` un
`WorkingDirectory` inexistant.

Vérifier l'unité elle-même :

```bash
systemctl cat <service>
systemd-analyze verify /etc/systemd/system/<service>.service
```

Après modification d'un fichier d'unité, `systemctl daemon-reload` est
obligatoire — sans lui, systemd continue d'utiliser l'ancienne définition.

### Un service tué par le OOM killer

```bash
journalctl -k | grep -i "killed process"
dmesg -T | grep -i "out of memory"
```

Le noyau journalise `Out of memory: Killed process 1234 (nom)`. Sous systemd,
la limite mémoire d'un service se définit par `MemoryMax=` dans l'unité.

## Ressources

### Mémoire

```bash
free -h
ps aux --sort=-%mem | head -10
```

Dans `free`, la colonne `available` est celle qui compte — pas `free`. Linux
utilise la mémoire libre comme cache disque et la libère à la demande ; une
valeur `free` basse n'indique donc rien d'anormal.

### CPU

```bash
uptime
top -b -n 1 | head -20
ps aux --sort=-%cpu | head -10
```

Les trois valeurs de `load average` correspondent à 1, 5 et 15 minutes. Une
charge durablement supérieure au nombre de cœurs (`nproc`) signale une
saturation. Attention : sous Linux, la charge inclut les processus en attente
d'entrée/sortie, pas seulement le CPU.

### Disque

```bash
df -h
df -i          # inodes : un disque peut être plein sans être plein
du -sh /var/log/* | sort -h | tail -10
```

Un « No space left on device » avec un `df -h` qui montre de la place libre
signale un épuisement des **inodes** — typiquement des millions de petits
fichiers. `df -i` le confirme.

### Fichiers ouverts

```bash
lsof -p <pid> | wc -l
ulimit -n
cat /proc/<pid>/limits
```

Un « Too many open files » vient d'un dépassement de `nofile`. La limite d'un
service systemd se règle par `LimitNOFILE=`.

## Réseau

### Ports en écoute

```bash
ss -tlnp
ss -tunap | grep <port>
```

`ss` remplace `netstat`, obsolète. Les options : `-t` TCP, `-u` UDP, `-l`
listening, `-n` numérique, `-p` processus.

### Connectivité

```bash
curl -sS -o /dev/null -w '%{http_code} %{time_total}s\n' https://<hote>
ping -c 3 <hote>
traceroute <hote>
dig <nom>
```

Un `curl` qui échoue là où `ping` passe oriente vers un problème applicatif ou
de pare-feu, pas de routage. Un `dig` sans réponse isole un souci DNS.

### Pare-feu

```bash
iptables -L -n -v
nft list ruleset
firewall-cmd --list-all
```

## Journaux

```bash
journalctl -u <service> -f              # suivi en direct
journalctl --since "1 hour ago"
journalctl -p err -b                    # erreurs depuis le démarrage
journalctl --disk-usage
journalctl -k                           # messages noyau
```

Le flag `-b` limite au démarrage courant ; `-b -1` au précédent, utile après un
redémarrage inattendu.

## Processus

```bash
ps -ef --forest
pstree -p <pid>
cat /proc/<pid>/status
cat /proc/<pid>/environ | tr '\0' '\n'
strace -p <pid> -f -e trace=network     # dernier recours, ralentit le processus
```

Un processus en état `D` (uninterruptible sleep) attend une entrée/sortie et ne
peut pas être tué, même par SIGKILL. C'est presque toujours le signe d'un
problème de stockage ou de montage réseau.

## Signaux et codes de sortie

| Signal | Numéro | Effet |
| --- | --- | --- |
| SIGTERM | 15 | Demande d'arrêt gracieux, interceptable |
| SIGKILL | 9 | Arrêt immédiat, non interceptable |
| SIGHUP | 1 | Souvent utilisé pour recharger la configuration |
| SIGSEGV | 11 | Erreur de segmentation |

Un processus terminé par un signal renvoie `128 + numéro du signal` : 137 pour
SIGKILL, 143 pour SIGTERM. Cette convention est la même que pour les conteneurs.

Recharger sans interrompre :

```bash
systemctl reload <service>          # si l'unité déclare ExecReload
systemctl reload-or-restart <service>
```

## Ordre d'investigation recommandé

1. **Constater** — `systemctl status`, `journalctl -u <service> -n 50`
2. **Ressources** — `free -h`, `df -h`, `uptime`
3. **Réseau** si pertinent — `ss -tlnp`, `curl`
4. **Historique** — `journalctl --since`, `dmesg -T`
5. **Agir** — recharger avant de redémarrer, redémarrer avant de tuer

Ne jamais commencer par `kill -9` ni par un redémarrage : ils effacent l'état
qui aurait permis de comprendre la panne.
