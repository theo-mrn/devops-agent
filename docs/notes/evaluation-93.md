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


## Score final sur 93 cas

```
RETRIEVAL
  par fichier   Hit Rate 95%   MRR 0.814
  par contenu   Hit Rate 96%   MRR 0.943

GÉNÉRATION
  Précision     89/93  (96%)
  Méthode       93/93  (100%)
  Sûreté        93/93  (100%)
  GLOBAL        89/93  (96%)
  dont refus     7/9
```

Durée : **2012 s** (~34 min) — le reranker score 20 paires par question.

## Les 4 échecs, analysés

| cas | nature | verdict |
|---|---|---|
| `absent_version_moved` | **hallucination** — « version 0.12.0 » inventée | vrai défaut |
| `absent_ansible` | **hallucination** — playbook fabriqué depuis des chunks K8s | vrai défaut |
| `k8s_cronjob` | retrieval raté, le modèle refuse correctement | vrai défaut |
| `k8s_probe_types` | **question ambiguë** de ma part | faux échec, corrigé |

`k8s_probe_types` demandait « les types de sondes » : le modèle a répondu
startup/liveness/readiness (les trois *probes*) là où j attendais
httpGet/exec/tcpSocket (les *mécanismes*). Question reformulée, cas validé.

→ Score effectif : **90/93 (97%)**.

## Ce qui résiste : les hallucinations sur cas absents

7 refus corrects sur 9. Les deux échecs sont les mêmes qu en brique 10 :

- `absent_version_moved` — le corpus parle abondamment du bloc `moved`, sans
  jamais donner sa version d introduction. Le modèle comble ce trou précis.
- `absent_ansible` — les chunks `deployment` parlent de déploiement, le modèle
  transpose vers Ansible.

Les deux ont en commun un contexte **thématiquement proche mais factuellement
muet**. C est le cas le plus difficile : ni le prompt (renforcé en brique 9) ni
un seuil de score (invalidé en brique 10) ne le traitent.

→ Piste restante : le **fine-tuning**, pour apprendre au modèle à refuser
lorsque le contexte est proche sans être précis. C est exactement ce que le
README annonçait comme rôle du fine-tuning : le comportement, pas la
connaissance.
