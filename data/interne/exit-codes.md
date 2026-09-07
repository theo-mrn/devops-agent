---
title: Codes de sortie des conteneurs Kubernetes
description: Table de référence des exit codes, leur cause et la procédure de diagnostic.
---

# Codes de sortie des conteneurs

Quand un conteneur s'arrête, Kubernetes enregistre son code de sortie dans
`status.containerStatuses[].lastState.terminated.exitCode`. Ce code identifie
la cause de l'arrêt.

## Table de référence

| Exit code | Signal | Cause | Champ à vérifier |
| --- | --- | --- | --- |
| 0 | — | Arrêt normal, le processus a terminé | `Reason: Completed` |
| 1 | — | Erreur applicative générique | logs du conteneur |
| 125 | — | Erreur du runtime de conteneur | événements du pod |
| 126 | — | Commande trouvée mais non exécutable | `command` / `entrypoint` |
| 127 | — | Commande introuvable dans l'image | `command` / `entrypoint` |
| 128 | — | Signal invalide | logs du kubelet |
| 137 | SIGKILL (9) | **OOMKilled** : dépassement de la limite mémoire | `Reason: OOMKilled` |
| 139 | SIGSEGV (11) | Erreur de segmentation dans l'application | logs du conteneur |
| 143 | SIGTERM (15) | Arrêt gracieux demandé par Kubernetes | `Reason: Terminated` |

La règle : un code supérieur à 128 correspond à `128 + numéro du signal`.
Ainsi 137 = 128 + 9 = SIGKILL, et 143 = 128 + 15 = SIGTERM.

## Exit code 137 : OOMKilled

### Ce que cela signifie

Le conteneur a été tué par le signal SIGKILL parce qu'il a dépassé la limite
mémoire définie dans `resources.limits.memory`. Le noyau Linux (cgroup memory
controller) déclenche l'OOM killer, qui termine le processus immédiatement.
Le conteneur ne reçoit aucun signal préalable et ne peut pas s'arrêter proprement.

Un dépassement de la limite **CPU** ne provoque jamais un kill : il provoque du
throttling, c'est-à-dire un ralentissement du conteneur. Confondre les deux est
une erreur de diagnostic fréquente.

### Comment le confirmer

La preuve se trouve dans le champ `Last State` du pod :

```bash
kubectl describe pod <nom-du-pod> -n <namespace>
```

Chercher dans la sortie :

```
    Last State:     Terminated
      Reason:       OOMKilled
      Exit Code:    137
```

Pour consulter les logs de l'instance qui a crashé — et non de celle qui vient
de redémarrer — le flag `--previous` est indispensable :

```bash
kubectl logs <nom-du-pod> -n <namespace> --previous --tail=100
```

Sans `--previous`, la commande retourne les logs du nouveau conteneur, qui ne
contiennent rien sur le crash.

### Procédure de diagnostic

1. Confirmer la cause avec `kubectl describe pod` et vérifier
   `Last State: Terminated / Reason: OOMKilled`.
2. Lire les logs de l'instance précédente avec `kubectl logs --previous`.
3. Comparer la consommation réelle et la limite configurée :
   `kubectl top pod <nom-du-pod> -n <namespace>`.
4. Inspecter les limites déclarées :
   `kubectl get pod <nom-du-pod> -o jsonpath='{.spec.containers[*].resources}'`.

### Résolution

Deux causes possibles, à distinguer avant d'agir :

**La limite est trop basse.** L'application a besoin de plus de mémoire que ce
qui lui est alloué. Augmenter `resources.limits.memory`, en gardant
`requests.memory` proche de la consommation nominale.

**L'application a une fuite mémoire.** La consommation croît continuellement
jusqu'à la limite. Augmenter la limite ne fait que retarder le crash : il faut
corriger l'application. Le signe distinctif est un OOMKilled qui se répète à
intervalle régulier après chaque redémarrage.

## Exit code 143 : SIGTERM

Le conteneur a reçu une demande d'arrêt gracieux, généralement lors d'un
`kubectl delete pod`, d'un rolling update ou d'une éviction. Ce n'est pas une
erreur.

Si l'application ne se termine pas dans le délai `terminationGracePeriodSeconds`
(30 secondes par défaut), Kubernetes envoie ensuite un SIGKILL et le code
devient 137. Un conteneur qui passe systématiquement de 143 à 137 ne gère pas
correctement SIGTERM.

## Exit codes 126 et 127

Ces deux codes indiquent un problème de commande, pas d'application :

- **127** : la commande n'existe pas dans l'image. Souvent un binaire absent
  d'une image `alpine` ou `distroless`, ou une faute de frappe dans `command`.
- **126** : la commande existe mais n'est pas exécutable — bit d'exécution
  manquant, ou script sans shebang.

Vérifier avec :

```bash
kubectl get pod <nom-du-pod> -o jsonpath='{.spec.containers[*].command}'
```
