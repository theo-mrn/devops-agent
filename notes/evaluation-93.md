# Brique 11 — Jeu d'évaluation élargi (93 cas)

De **39 à 93 cas**. Objectif : qu'un écart de 2-3 points cesse d'être du bruit.
À 39 cas, un cas vaut 2,6 points ; à 93, il en vaut 1,1.

## Répartition

| catégorie | cas |
|---|---:|
| diagnostic (pannes réelles) | 14 |
| sécurité / pièges | 8 |
| Kubernetes config | ~30 |
| Terraform langage + state | ~32 |
| absent (doit refuser) | 9 |

## Méthode de rédaction

**Chaque sujet a été vérifié présent dans le corpus AVANT d'écrire le cas.**
Un cas dont la réponse n'existe pas mesure la mauvaise chose.

Sujets écartés faute de couverture : `networkpolicy` (0 fichier),
`topologySpread` (1), PVC (le fichier attendu n'existait pas).

## Les cas « absent » : vérification obligatoire

Le piège de la brique 9 (Helm présent dans 3 fichiers, donc le modèle
extrapolait légitimement) impose de vérifier chaque sujet à **zéro occurrence** :

| sujet testé | occurrences | retenu |
|---|---:|---|
| jenkins | 1 | ❌ remplacé par CircleCI (0) |
| nginx.conf | 1 | ❌ remplacé par Puppet (0) |
| kafka | 0 | ✅ |
| poetry | 0 | ✅ |

## Retrieval sur 93 cas

```
par fichier   Hit Rate 95%   MRR 0.814
par contenu   Hit Rate 96%   MRR 0.943
```

Les scores tiennent à cette échelle plus exigeante (85 cas mesurables contre 34).

## Quatre échecs de retrieval, réels

- `k8s_cronjob` — `cron-jobs.md` existe mais ne remonte pas
- `diag_node_notready` — le sujet est éclaté entre plusieurs fichiers
- `surete_tf_state_edit` — question trop vague pour cibler un document
- `tf_remote_state` — échec persistant depuis la brique 8

Aucun n'est un artefact de test : les fichiers attendus existent bien.

## Correction d'une erreur de rédaction

Trois `fichier_attendu` avaient été écrits sans vérifier l'existence du fichier.
`configure-persistent-volume-storage` n'existe pas dans le corpus — cas retiré
plutôt que de mesurer un échec inventé.

→ **Écrire un cas de test suppose de connaître le corpus.**
