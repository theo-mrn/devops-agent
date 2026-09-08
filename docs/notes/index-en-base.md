# Index vectoriel en base

## Pourquoi pas un fichier

`corpus.pkl` convenait au développement local, pas à un déploiement :

**Il contient le texte des chunks.** Indexer un dépôt GitOps privé y met les
manifests — donc `clientSecret`, `adminPassword`, la topologie interne.
L'embarquer dans une image publique les exposerait.

**Le lire suppose PyTorch.** Charger `sentence-transformers` dans le pod fait
passer l'image de 66 Mo à ~2,5 Go, pour un agent dont c'est un outil sur six.

**Il faut le régénérer et redéployer** à chaque changement du dépôt.

## L'architecture retenue

```
CronJob (toutes les 6 h)
  ├─ initContainer : clone le dépôt privé (deploy key en lecture seule)
  └─ indexeur      : encode et écrit dans PostgreSQL
                            ↓
                      pgvector
                            ↓
Agent → SELECT … ORDER BY vecteur <=> question
```

**Rien ne sort du cluster.** Le dépôt privé n'est lu que par un job qui vit à
côté de la base.

**L'agent reste léger.** La similarité est calculée par PostgreSQL ; il n'a
besoin que de `psycopg` pour interroger, jamais de PyTorch.

## pgvector sans image personnalisée

L'extension est présente dans l'image standard CloudNativePG :

```
$ kubectl exec <pod> -- psql -tAc \
    "SELECT name FROM pg_available_extensions WHERE name='vector';"
vector
```

Le cluster déclare simplement `postInitSQL: [CREATE EXTENSION vector]`.

## Choix de schéma

**IVFFlat plutôt que HNSW.** Recherche approchée, construction rapide,
largement suffisante sur quelques milliers de chunks. HNSW serait plus précis
mais coûte davantage à construire — sans gain perceptible à cette échelle.

**Une empreinte SHA-256 unique** par chunk rend l'insertion idempotente :
réindexer ne duplique jamais.

**Le remplacement se fait par source, dans une transaction.** À aucun moment
l'agent ne voit un index partiel : soit l'ancien, soit le nouveau.

## Encodage de la question

L'agent doit encore transformer la question en vecteur. Deux voies :

```bash
RAG_EMBED_URL=http://embed.devops-agent.svc:8080/vecteur   # service dédié
# à défaut : sentence-transformers local, si installé
```

Sans l'un ni l'autre, la recherche est indisponible et l'agent poursuit avec
ses cinq autres outils — testé.

## Sûreté

**Le clone est isolé dans un initContainer.** L'indexeur n'a jamais accès à la
clé SSH : elle n'est montée que dans le conteneur qui clone.

**La clé est une deploy key en lecture seule**, propre à ce dépôt. Elle ne
donne accès à rien d'autre.

**Le job tourne en non-root**, avec le même ServiceAccount que l'agent — donc
sans aucun droit d'écriture sur le cluster.

## Deux images

| image | contenu | taille |
|---|---|---|
| `devops-agent:X.Y.Z` | l'agent, `psycopg` | 66 Mo |
| `devops-agent:X.Y.Z-rag` | + PyTorch, sentence-transformers | ~2,5 Go |

La seconde ne tourne que six fois par jour, quelques minutes. La première
tourne en continu.
