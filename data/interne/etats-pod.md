---
title: États et raisons d'échec des pods Kubernetes
description: Référence des états anormaux d'un pod, leur cause et la procédure d'investigation.
---

# États anormaux d'un pod

## Table de référence

| État | Cause principale | Première commande |
| --- | --- | --- |
| `Pending` | Aucun nœud ne peut accueillir le pod | `kubectl describe pod` |
| `ContainerCreating` | Volume, image ou réseau en cours de préparation | `kubectl describe pod` |
| `CrashLoopBackOff` | Le conteneur redémarre en boucle | `kubectl logs --previous` |
| `ImagePullBackOff` | L'image ne peut pas être téléchargée | `kubectl describe pod` |
| `ErrImagePull` | Échec immédiat du téléchargement d'image | `kubectl describe pod` |
| `CreateContainerConfigError` | ConfigMap ou Secret référencé absent | `kubectl describe pod` |
| `Terminating` | Suppression en cours, parfois bloquée | `kubectl get pod -o yaml` |
| `Evicted` | Le nœud a expulsé le pod par manque de ressources | `kubectl describe node` |

## CrashLoopBackOff

Le conteneur démarre, s'arrête, et Kubernetes le relance avec un délai
croissant : 10 s, 20 s, 40 s, jusqu'à 5 minutes. L'état lui-même n'indique pas
la cause — il faut la chercher dans le code de sortie.

### Investigation

```bash
kubectl describe pod <nom-du-pod> -n <namespace>
kubectl logs <nom-du-pod> -n <namespace> --previous --tail=100
```

Le flag `--previous` est indispensable : sans lui, on lit les logs du conteneur
qui vient de démarrer, pas de celui qui a crashé.

### Causes fréquentes, par code de sortie

- **137** — OOMKilled, la limite mémoire est dépassée.
- **1** — erreur applicative, la cause est dans les logs.
- **127** — commande introuvable dans l'image.
- **0 en boucle** — le processus principal se termine normalement mais ne reste
  pas au premier plan. Typique d'un script qui lance un démon en arrière-plan.

Un liveness probe mal configuré produit aussi un CrashLoopBackOff : la sonde
échoue, Kubernetes tue le conteneur, qui redémarre et échoue à nouveau. Vérifier
que `initialDelaySeconds` laisse à l'application le temps de démarrer.

## Pending

Le pod est accepté par l'API mais aucun nœud ne peut l'accueillir.

```bash
kubectl describe pod <nom-du-pod> -n <namespace>
```

La section `Events` donne la raison exacte. Les causes usuelles :

- **Insufficient cpu / memory** — aucun nœud n'a les ressources demandées dans
  `resources.requests`. Vérifier avec `kubectl describe node`.
- **node(s) had untolerated taint** — les nœuds portent un taint que le pod ne
  tolère pas.
- **didn't match node selector** — le `nodeSelector` ou l'`affinity` ne
  correspond à aucun nœud.
- **pod has unbound immediate PersistentVolumeClaims** — le PVC n'est pas lié à
  un volume.

## ImagePullBackOff et ErrImagePull

Kubernetes ne parvient pas à télécharger l'image. `ErrImagePull` est l'échec
immédiat ; `ImagePullBackOff` signale que Kubernetes réessaie avec un délai
croissant.

```bash
kubectl describe pod <nom-du-pod> -n <namespace>
```

Les événements précisent la cause :

- **manifest unknown / not found** — le tag n'existe pas. Vérifier le nom exact
  de l'image et son tag.
- **unauthorized / authentication required** — registre privé sans
  `imagePullSecrets`, ou secret invalide.
- **no such host / timeout** — le nœud ne joint pas le registre. Problème réseau
  ou DNS.

Vérifier le secret de registre :

```bash
kubectl get secret <nom-du-secret> -n <namespace> -o jsonpath='{.type}'
```

Le type attendu est `kubernetes.io/dockerconfigjson`.

## Terminating bloqué

Un pod qui reste en `Terminating` plusieurs minutes est généralement retenu par
un finalizer ou par un volume qui ne se démonte pas.

### Investigation, avant toute action

```bash
kubectl get pod <nom-du-pod> -n <namespace> -o jsonpath='{.metadata.finalizers}'
kubectl describe pod <nom-du-pod> -n <namespace>
```

Vérifier ensuite les logs du kubelet sur le nœud concerné.

### Suppression forcée

`kubectl delete pod --grace-period=0 --force` retire l'objet de l'API sans
attendre que le kubelet confirme l'arrêt du conteneur. Le conteneur peut
continuer à tourner sur le nœud, et ses volumes rester montés.

C'est une commande de dernier recours, à n'utiliser qu'après avoir identifié la
cause du blocage. Sur un StatefulSet, elle peut provoquer une double écriture
si le pod est recréé pendant que l'ancien tourne encore.

## Evicted

Le kubelet a expulsé le pod parce que le nœud manquait de ressources — mémoire,
espace disque ou PID.

```bash
kubectl describe pod <nom-du-pod> -n <namespace>
kubectl describe node <nom-du-noeud>
```

Le message d'éviction indique la ressource en cause. À la différence d'un
OOMKilled, qui concerne un conteneur dépassant *sa* limite, l'éviction concerne
le *nœud entier* sous pression. Les pods de classe QoS `BestEffort` sont
expulsés en premier, puis `Burstable`, et enfin `Guaranteed`.

Définir des `requests` réalistes protège un pod de l'éviction.
