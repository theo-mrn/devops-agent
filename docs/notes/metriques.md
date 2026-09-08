# Détection par métriques

## Ce que ça apporte

Les détecteurs d'objets voient qu'un pod **a été** tué. Les métriques
permettent de voir qu'il **va** l'être.

Sur le cluster réel, au premier relevé :

```
Pression mémoire :
  sonarqube/sonarqube-sonarqube-0   1712Mi / 2048Mi  (84 %)
  monitoring/prometheus-…-0          806Mi / 1024Mi  (79 %)
  9 pod(s) sans limite mémoire déclarée
```

`sonarqube-0` est le pod déjà OOMKilled une fois. À 84 %, il est juste sous
le seuil : l'agent alertera **avant** le prochain kill au lieu de le
constater après.

Prometheus à 79 % était invisible jusqu'ici.

## Le piège évité : le bruit

Une métrique instantanée est bruyante. Un pod peut monter à 90 % pendant dix
secondes lors d'un pic normal — chaque pic coûterait un diagnostic.

**Trois relevés consécutifs** au-dessus du seuil sont exigés, à 60 secondes
d'intervalle. Une série rompue remet le compteur à zéro : haut, bas, haut ne
déclenche rien. Un test le vérifie explicitement.

Un pod déjà signalé ne l'est pas à nouveau avant une heure.

## Ce qui est détecté

| signal | seuil |
|---|---|
| `MemoireProcheDeLaLimite` | consommation ≥ 85 % de `limits.memory` |

Les pods **sans limite déclarée** sont comptés dans l'aperçu mais ne
déclenchent rien : sans limite, aucun ratio n'est calculable. Ils restent
exposés à l'éviction du nœud.

## Ce que metrics-server ne permet pas

| | metrics-server | il faudrait Prometheus |
|---|---|---|
| consommation instantanée | ✅ | |
| tendance sur 24 h | | ✅ |
| latence, taux d'erreur | | ✅ |
| saturation des volumes | | ✅ |
| certificats expirants | | ✅ |

C'est pourquoi le flux Alertmanager reste en place : il couvre ce que l'agent
ne voit pas.

## Unités

metrics-server et les manifests n'emploient pas les mêmes :

```
metrics-server : 69136Ki   12700236n
manifests      : 2Gi       500m
```

Les deux sont normalisés avant comparaison — sans quoi tout ratio serait faux.

## Réglages

| variable | rôle | défaut |
|---|---|---|
| `RAG_SEUIL_MEMOIRE` | part de la limite | 0.85 |
| `RAG_RELEVES_CONFIRMATION` | relevés consécutifs exigés | 3 |
| `RAG_INTERVALLE_METRIQUES` | secondes entre relevés | 60 |

## Coût

Le relevé passe par l'API Kubernetes : **aucun appel au modèle**. Un test
vérifie que le module n'importe jamais de client LLM.

Seul le diagnostic déclenché coûte, comme pour les autres anomalies.
