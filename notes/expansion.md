# Brique 13 — Expansion de requête

## Le problème découvert

Le retrieval échouait sur des questions posées en **français courant**, alors
que les mêmes questions formulées avec le **vocabulaire du corpus** réussissaient
parfaitement.

| question | rang | score |
|---|---:|---:|
| « planifier une tâche récurrente » | — | 0,016 |
| « utiliser un **CronJob** » | **1** | **0,779** |
| « partager des fichiers entre jobs » | — | 0,057 |
| « utiliser les **artifacts** » | **1** | **0,987** |

**Le même besoin, exprimé avec le terme technique, passe de l'échec au rang 1.**

C'est le défaut le plus important trouvé depuis longtemps, parce qu'il touche
l'usage réel : un ingénieur français ne dit pas « CronJob », il dit « tâche
planifiée ». Ni l'embedding ni BM25 ni le reranker ne font ce lien — c'est de la
connaissance métier, absente des trois mécanismes.

## La solution

Un glossaire **expression courante → termes du corpus**, appliqué avant la
recherche. ~70 entrées couvrant Kubernetes, Docker, Linux, CI/CD et Terraform.

Deux choix de conception :

1. **L'expansion AJOUTE, elle ne remplace pas.** La formulation d'origine est
   conservée : le retrieval garde le sens de la phrase et gagne les mots-clés.
2. **Elle s'applique à la RECHERCHE uniquement**, pas au prompt envoyé au
   modèle — sinon la question devient illisible pour lui.

Branchée sur les trois étages : dense, BM25 et le cross-encoder du reranker.

### Correspondance souple

Une correspondance par expression figée ratait « le disque **est** plein » face à
la clé « disque plein ». La règle retenue : **tous les mots significatifs de la
clé présents dans la question**, pas nécessairement contigus.

## Résultat

| | avant | après |
|---|---:|---:|
| Hit Rate @5 (fichier) | 95% | **100%** |
| MRR fichier | 0,818 | **0,861** |
| MRR contenu | 0,942 | **0,948** |
| échecs de retrieval | 5 | **0** |

## Deux corrections de cas de test au passage

**`docker_disk_full`** n'était pas un échec : `pruning` sortait au rang 1 avec
0,738. Mon `fichier_attendu` disait `prune`, le fichier s'appelle `pruning` —
la sous-chaîne ne correspondait pas.

**`diag_node_notready`** attendait `monitor-node-health`, qui traite en réalité
du *Node Problem Detector*, un outil de monitoring — pas du diagnostic d'un nœud
NotReady. **Le corpus ne couvre pas ce sujet.** Cas reformulé vers ce qui existe
réellement plutôt que de mesurer un manque inventé.

## Limite de l'approche

Le glossaire est **écrit à la main** : il ne couvre que ce qu'on y a mis. Une
expression absente ne sera pas étendue. C'est le compromis assumé — une expansion
automatique par LLM coûterait un appel supplémentaire par requête, pour un gain
incertain sur un vocabulaire technique aussi précis.
