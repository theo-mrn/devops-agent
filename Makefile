# Commandes du projet. `make` seul affiche cette aide.

.DEFAULT_GOAL := help
.PHONY: help baseline rag ask chunks embed check

help:  ## Affiche cette aide
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk -F':.*?## ' '{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

baseline:  ## Questions de référence sur le modèle NU (sans RAG)
	@uv run python src/baseline.py

rag:  ## Mêmes questions AVEC le RAG
	@uv run python src/rag.py

ask:  ## Question libre avec RAG : make ask Q="ta question"
	@uv run python src/rag.py "$(Q)"

chunks:  ## Compare découpage naïf et structurel
	@uv run python src/comparer.py

embed:  ## Matrice de similarité entre phrases témoins
	@uv run python src/embeddings.py

check:  ## Vérifie que la stack locale répond
	@echo "Ollama  : $$(curl -s localhost:11434/api/version 2>/dev/null || echo 'hors service')"
	@echo "Modèles : $$(ollama list 2>/dev/null | tail -n +2 | wc -l | tr -d ' ') installé(s)"
	@echo "RAM     : $$(memory_pressure 2>/dev/null | grep -i 'free percentage' || echo 'n/a')"
