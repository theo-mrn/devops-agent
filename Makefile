# Commandes du projet. `make` seul affiche cette aide.

.DEFAULT_GOAL := help
.PHONY: help baseline ask check

help:  ## Affiche cette aide
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk -F':.*?## ' '{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

baseline:  ## Lance les 3 questions de référence sur le modèle nu
	@uv run python src/baseline.py

ask:  ## Pose une question libre : make ask Q="ta question"
	@uv run python src/baseline.py "$(Q)"

check:  ## Vérifie que la stack locale répond
	@echo "Ollama  : $$(curl -s localhost:11434/api/version 2>/dev/null || echo 'hors service')"
	@echo "Modèles : $$(ollama list 2>/dev/null | tail -n +2 | wc -l | tr -d ' ') installé(s)"
	@echo "RAM     : $$(memory_pressure 2>/dev/null | grep -i 'free percentage' || echo 'n/a')"
