"""Alimente l'index vectoriel de la base depuis un dépôt GitOps.

Exécuté par un CronJob dans le cluster : le dépôt privé est cloné à
côté, indexé, et le résultat écrit en base. Rien ne quitte le cluster.

    devops-agent reindexer

Le remplacement se fait par source et dans une transaction : à aucun
moment l'agent ne voit un index partiel.
"""

import os
import sys
import time
from pathlib import Path

from devops_agent.retrieval import vectordb


def _encoder_lot(textes: list[str]) -> list[list[float]]:
    """Encode les chunks. C'est l'étape coûteuse — d'où le CronJob."""
    import torch
    from sentence_transformers import SentenceTransformer

    from devops_agent.core import config

    device = "cuda" if torch.cuda.is_available() else "cpu"
    modele = SentenceTransformer(config.EMBEDDING, device=device)
    vecteurs = modele.encode(
        textes, normalize_embeddings=True, batch_size=8, show_progress_bar=False
    )
    return [v.tolist() for v in vecteurs]


def main() -> int:
    depot = os.environ.get("RAG_INFRA_REPO", "")
    if not depot:
        print("RAG_INFRA_REPO n'est pas défini", file=sys.stderr)
        return 1

    racine = Path(depot)
    if not racine.exists():
        print(f"dépôt introuvable : {racine}", file=sys.stderr)
        return 1

    if not vectordb.disponible():
        print("base indisponible — vérifier RAG_DB_DSN et psycopg",
              file=sys.stderr)
        return 1

    from devops_agent.ingestion import manifests

    print(f"\033[1m INDEXATION \033[0m {racine}")
    vectordb.initialiser()

    total = 0
    for source, collecteur in (
        ("infra", manifests.collecter),
        ("runbook", manifests.collecter_documentation),
    ):
        chunks = collecteur(racine)
        if not chunks:
            print(f"  {source:<10} aucun contenu")
            continue

        debut = time.time()
        vecteurs = _encoder_lot([c["texte"] for c in chunks])
        supprimes, inseres = vectordb.remplacer_source(source, chunks, vecteurs)

        print(f"  {source:<10} {inseres} chunks "
              f"(remplace {supprimes}) · {time.time() - debut:.0f}s")
        total += inseres

    print(f"\n\033[32m✓\033[0m {total} chunks indexés")
    for source, nombre in vectordb.statistiques().items():
        print(f"    {source:<12} {nombre}")
    return 0
