# Commandes du projet. `make` seul affiche cette aide.

.DEFAULT_GOAL := help
.PHONY: help baseline rag ask chunks embed eval eval-fast eval-cat ablation methodes corpus index check

help:  ## Affiche cette aide
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk -F':.*?## ' '{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

baseline:  ## Questions de référence sur le modèle NU (sans RAG)
	@uv run python src/baseline.py

rag:  ## Questions de démonstration sur le corpus complet
	@uv run python src/rag2.py 2>/dev/null

ask:  ## Question libre : make ask Q="ta question"
	@uv run python src/rag2.py "$(Q)" 2>/dev/null

corpus:  ## Télécharge les documents sources
	@uv run python src/telecharger.py

index:  ## Reconstruit l'index vectoriel (387 chunks, ~30s)
	@uv run python src/corpus.py 2>/dev/null

chunks:  ## Compare découpage naïf et structurel
	@uv run python src/comparer.py

embed:  ## Matrice de similarité entre phrases témoins
	@uv run python src/embeddings.py

eval:  ## Score complet, 115 cas (~30 min) - a reserver aux fins de chantier
	@uv run python src/evaluer3.py 2>/dev/null

eval-fast:  ## Retrieval seul, 115 cas (~3 min) - suffit pour 80% des decisions
	@uv run python src/evaluer3.py --retrieval-seul 2>/dev/null

eval-cat:  ## Une seule categorie (~2 min) : make eval-cat C=docker
	@uv run python src/evaluer3.py --categorie $(C) 2>/dev/null

ablation:  ## Compare les stratégies de chunking
	@uv run python src/ablation.py 2>/dev/null

methodes:  ## Compare dense / BM25 / hybride sur le jeu de test
	@uv run python src/ablation2.py 2>/dev/null

check:  ## Vérifie que la stack locale répond
	@echo "Ollama  : $$(curl -s localhost:11434/api/version 2>/dev/null || echo 'hors service')"
	@echo "Modèles : $$(ollama list 2>/dev/null | tail -n +2 | wc -l | tr -d ' ') installé(s)"
	@echo "RAM     : $$(memory_pressure 2>/dev/null | grep -i 'free percentage' || echo 'n/a')"
