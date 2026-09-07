# Commandes du projet. `make` seul affiche cette aide.

.DEFAULT_GOAL := help
.PHONY: help ask rag corpus index eval eval-fast eval-cat methodes expansion config check archive

help:  ## Affiche cette aide
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | \
		awk -F':.*?## ' '{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

# ── Usage ────────────────────────────────────────────────────────

ask:  ## Question libre : make ask Q="ta question"  (RAG_PROVIDER=anthropic pour l API)
	@uv run python src/rag2.py "$(Q)" 2>/dev/null

rag:  ## Questions de démonstration
	@uv run python src/rag2.py 2>/dev/null

# ── Corpus ───────────────────────────────────────────────────────

corpus:  ## Télécharge les documents sources (~2 min, réseau)
	@uv run python src/telecharger.py

index:  ## Reconstruit l'index vectoriel (~2,5 min de GPU)
	@uv run python src/corpus.py 2>/dev/null

# ── Mesure ───────────────────────────────────────────────────────

eval-fast:  ## Retrieval seul (~3 min) — suffit pour 80% des décisions
	@uv run python src/evaluer3.py --retrieval-seul 2>/dev/null

eval-cat:  ## Une catégorie (~2 min) : make eval-cat C=docker
	@uv run python src/evaluer3.py --categorie $(C) 2>/dev/null

eval:  ## Score complet, 115 cas (~35 min) — à réserver aux fins de chantier
	@uv run python src/evaluer3.py 2>/dev/null

methodes:  ## Compare dense / BM25 / hybride / reranker
	@uv run python src/ablation2.py 2>/dev/null

expansion:  ## Montre les termes ajoutés par le glossaire
	@uv run python src/expansion.py "$(Q)"

# ── Divers ───────────────────────────────────────────────────────

config:  ## Affiche les modèles et paramètres actifs
	@uv run python src/config.py 2>/dev/null

check:  ## Vérifie que la stack locale répond
	@echo "Ollama  : $$(curl -s -m 5 localhost:11434/api/version 2>/dev/null || echo 'hors service')"
	@echo "Modèles : $$(ollama list 2>/dev/null | tail -n +2 | wc -l | tr -d ' ') installé(s)"
	@echo "Index   : $$(du -h data/index/corpus.pkl 2>/dev/null | cut -f1) · $$(ls data/raw 2>/dev/null | wc -l | tr -d ' ') fichiers sources"
	@echo "RAM     : $$(memory_pressure 2>/dev/null | grep -i 'free percentage' | sed 's/^.*: //')"

archive:  ## Liste les scripts archivés (démonstrations des briques 1-3)
	@cat src/archive/README.md
