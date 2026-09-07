# Scripts archivés

Ces scripts ne font plus partie du pipeline actif. Ils documentent des étapes
du projet et restent exécutables (`uv run python src/archive/<script>.py`).

| script | brique | ce qu'il montre |
|---|---|---|
| `baseline.py` | 1 | le modèle nu, sans RAG — l'hallucination de départ |
| `chunk_naif.py` | 2 | découpage tous les N caractères, 3 chunks cassés sur 6 |
| `comparer.py` | 2 | naïf vs structurel, côte à côte |
| `embeddings.py` | 3 | matrice de similarité — ImagePullBackOff classé plus proche d'« exit 137 » qu'OOMKilled |
| `recherche.py` | 3 | première recherche sémantique, sur 5 chunks |

Le pipeline actif est décrit dans le README à la racine.
