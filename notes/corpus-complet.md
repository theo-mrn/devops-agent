# Brique 9 — Corpus complété

De **40 à 107 fichiers**, de **387 à 1028 chunks** (1,3 Mo).

## Deux volets

### 1. Élargir les sources externes

Ajout de 4 sections Kubernetes : `scheduling-eviction` (18 fichiers),
`debug-cluster`, `workloads/controllers`, `configure-pod-container`.

**Résultat décevant sur le point critique** : `OOMKilled` passe de **1 à 2
fichiers** seulement. La documentation Kubernetes officielle ne contient pas de
table des codes de sortie.

### 2. Rédiger ce qui n'existe nulle part

C'est le volet décisif. Deux documents internes écrits à la main :

- `data/interne/exit-codes.md` — table des codes de sortie (0, 1, 125-128, 137,
  139, 143), la règle `128 + signal`, la procédure de diagnostic OOMKilled,
  la distinction limite-trop-basse / fuite mémoire.
- `data/interne/etats-pod.md` — états anormaux (Pending, CrashLoopBackOff,
  ImagePullBackOff, Terminating, Evicted), causes et commandes d'investigation.

**Un corpus RAG n'est pas seulement de la doc récupérée.** C'est aussi de la
connaissance métier rédigée pour combler ce que les sources publiques ne
couvrent pas. 11 chunks sur 1028, mais ce sont les plus utiles.

## Le cas de référence, avant / après

| | avant | après |
|---|---|---|
| Source #1 | `pod-lifecycle` (générique) | **`interne/etats-pod — CrashLoopBackOff`** |
| Diagnostic | « signal 9… mémoire **ou CPU** » | « **OOMKilled**, limite mémoire dépassée » |
| Commande | `kubectl logs` | `kubectl logs **--previous** --tail=100` |
| Confusion CPU/mémoire | présente | **absente** |

## TOP_K : 3 → 5

| top-K | Hit Rate | MRR | échecs |
|---|---:|---:|---|
| 3 | 91% | 0,848 | 3 |
| **5** | **97%** | **0,861** | 1 |
| 8 | 97% | 0,861 | 1 |

Au-delà de 5, aucun gain. Réglage retenu : **5**.

## ⚠️ Trois pièges rencontrés

### 1. Un corpus élargi invalide les cas de test

La métrique « par fichier » est tombée de 97% à 88% — **artefact**. Les
`fichier_attendu` avaient été écrits pour l'ancien corpus : quand
`interne/etats-pod` remonte au rang 1 avec la meilleure réponse, le test le
comptait comme un échec.

→ Après mise à jour des cas : 97%. **Un jeu de test se maintient en même temps
que le corpus.**

### 2. Un cas « absent » cesse de l'être

`absent_helm` échouait : le modèle répondait `helm create`. Vérification : le
corpus élargi mentionne Helm dans **3 fichiers**. Ce n'était plus un vrai cas
absent — le modèle extrapolait à partir d'une mention réelle, pas de rien.

→ Remplacé par une question GitLab CI (0 occurrence vérifiée dans le corpus).

### 3. Le seuil de pertinence ne fonctionne pas

Tentative d'un garde-fou « si le meilleur score < seuil, refuser ». **Échec.**

| question | score RRF | score dense |
|---|---:|---:|
| Ansible (hors domaine) | 0,0270 | 0,5877 |
| bloc `moved` (dans le corpus) | 0,0323 | 0,5755 |

**Le hors-domaine score PLUS HAUT que le pertinent.** Ni RRF ni dense ne
discriminent.

Pour RRF c'est structurel : il calcule à partir de **rangs**, pas de scores. Le
1er obtient toujours `1/61 + 1/61 = 0,0328`, pertinent ou non. **RRF détruit
l'information de pertinence absolue.**

→ Code retiré plutôt que laissé inopérant. Confirme la leçon de la brique 3 :
les valeurs absolues de similarité ne veulent rien dire.

## Prompt durci

Ajout de règles explicites : vérifier qu'une commande figure littéralement dans
le contexte avant de la citer ; refuser même si le contexte parle d'un sujet
proche ; conditions précises pour les commandes destructives ;
`kubectl logs --previous` après un redémarrage.

**Le prompt seul n'a pas suffi** à empêcher l'extrapolation Helm — parce que la
cause était dans le corpus, pas dans le prompt. Enseignement : on ne corrige pas
tout par le prompt.

## Performance

- 1028 chunks encodés : **78 s** sur MPS (13,2 chunks/s)
- index : **5,3 Mo**
- évaluation complète (39 cas, top-5) : ~9 min
