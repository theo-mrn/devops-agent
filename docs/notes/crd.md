# Ressources personnalisées dans le RBAC

## Le manque, constaté en conditions réelles

Lors du diagnostic de `n8n-postgres-ro`, l'agent avait écrit :

> Accès direct à la ressource `Cluster n8n-postgres` refusé (RBAC), donc
> impossible de confirmer via le CR le nombre d'instances déclaré, mais
> l'absence totale de pod replica dans le namespace le confirme
> indirectement.

Il avait raison sur le fond, mais par déduction plutôt que par constat. Un pod
géré par un opérateur n'a de sens qu'au regard de la ressource qui le définit.

## Ce qui a été ajouté

Huit groupes, **en lecture seule**, choisis d'après les CRD réellement
présentes sur le cluster :

| groupe | ce qu'il apporte au diagnostic |
|---|---|
| `postgresql.cnpg.io` | nombre d'instances, sauvegardes, bascules |
| `argoproj.io` | état de synchronisation, dérive GitOps |
| `monitoring.coreos.com` | règles d'alerte, cibles de collecte |
| `traefik.io` | routage — un service injoignable vient souvent de là |
| `cert-manager.io` | certificats non renouvelés |
| `aquasecurity.github.io` | rapports de vulnérabilité |
| `snapshot.storage.k8s.io` | restauration après incident de stockage |
| `helm.cattle.io` | état des déploiements par chart |

## Vérification après application

```
clusters.postgresql.cnpg.io                    yes     ← lecture
applications.argoproj.io                       yes
ingressroutes.traefik.io                       yes
vulnerabilityreports.aquasecurity.github.io    yes

delete clusters.postgresql.cnpg.io             no      ← écriture
patch  clusters.postgresql.cnpg.io             no
create applications.argoproj.io                no
```

Et le cas qui avait échoué :

```
$ kubectl get cluster n8n-postgres -n n8n -o jsonpath='{.spec.instances}'
1
```

L'agent peut désormais conclure directement plutôt que par déduction.

## Pourquoi pas un joker

`apiGroups: ["*"]` aurait été plus court, mais ajouter un opérateur au cluster
donnerait alors un accès automatique à ses ressources — sans décision.

Les groupes sont nommés explicitement, et trois tests le garantissent : aucun
joker de groupe, aucun verbe d'écriture sur une CRD, présence des groupes
attendus.
