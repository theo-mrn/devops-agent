# devops-agent — `make` seul affiche cette aide.

.DEFAULT_GOAL := help
.PHONY: help install ask diagnose watch watch-diagnose fetch index eval eval-fast eval-cat test config clean

help:  ## Affiche cette aide
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk -F':.*?## ' '{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## Installe le paquet et ses dépendances
	@uv sync --all-extras

# ── Usage ────────────────────────────────────────────────────────

ask:  ## Interroge la documentation : make ask Q="ta question"
	@uv run devops-agent ask "$(Q)" 2>/dev/null

diagnose:  ## Agent outillé (API requise) : make diagnose Q="..."
	@uv run devops-agent diagnose "$(Q)"

watch:  ## Surveille le cluster (observation, ne coûte rien)
	@uv run devops-agent watch

watch-diagnose:  ## Surveille ET diagnostique (coûte de l API)
	@uv run devops-agent watch --diagnose

# ── Corpus ───────────────────────────────────────────────────────

fetch:  ## Télécharge les sources documentaires (~2 min, réseau)
	@uv run devops-agent fetch

index:  ## Reconstruit l'index vectoriel (~2,5 min de GPU)
	@uv run devops-agent index 2>/dev/null

# ── Qualité ──────────────────────────────────────────────────────

test:  ## Tests unitaires (instantanés, sans réseau)
	@uv run pytest tests/ -q

eval-fast:  ## Retrieval seul (~3 min) — suffit pour 80% des décisions
	@uv run devops-agent eval --fast 2>/dev/null

eval-cat:  ## Une catégorie (~2 min) : make eval-cat C=docker
	@uv run devops-agent eval --cat $(C) 2>/dev/null

eval:  ## Score complet, 115 cas (~35 min) — fins de chantier
	@uv run devops-agent eval 2>/dev/null

# ── Divers ───────────────────────────────────────────────────────

config:  ## Affiche la configuration active
	@uv run devops-agent config 2>/dev/null

clean:  ## Supprime les caches Python
	@find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null; true
	@find . -name "*.pyc" -delete 2>/dev/null; true
	@rm -rf .pytest_cache
