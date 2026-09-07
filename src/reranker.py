"""Reranker : réordonner les candidats en lisant question ET document ensemble.

Différence fondamentale avec le retrieval :
  - dense/BM25 comparent des représentations calculées SÉPARÉMENT
    (le vecteur du document ne sait rien de la question) ;
  - le reranker (cross-encoder) lit la paire (question, document) en une
    seule passe et produit un score de pertinence directe.

C'est bien plus précis, mais bien plus lent : on ne peut pas l'appliquer
aux 1427 chunks. D'où le schéma en deux étapes :
    hybride → 20 candidats → reranker → top 5

Usage :
    uv run python src/reranker.py "ta question"
"""

import sys
import threading

import torch
from sentence_transformers import CrossEncoder

import expansion
import rag2

MODELE = "BAAI/bge-reranker-v2-m3"
CANDIDATS = 20  # nombre de chunks à faire réordonner

# Un seuil de pertinence a été tenté ici, puis ABANDONNÉ.
#
# Sur 10 questions choisies, la séparation semblait nette (dans le corpus
# 0,745-0,975 ; hors corpus 0,010-0,595). Mesuré sur les 39 cas réels, les
# deux populations se chevauchent complètement :
#   plus bas DANS le corpus  : 0,079  (surete_node_charge)
#   plus haut HORS corpus    : 0,597  (absent_version_moved)
# Aucun seuil ne les sépare. Le garde-fou cassait 5 cas légitimes pour en
# sauver 2. C'est le prompt, pas un seuil, qui gère le hors-domaine.

_cache = {}
_verrou = threading.Lock()


def charger() -> CrossEncoder:
    with _verrou:
        if "modele" not in _cache:
            device = "mps" if torch.backends.mps.is_available() else "cpu"
            _cache["modele"] = CrossEncoder(MODELE, device=device, max_length=512)
    return _cache["modele"]


def chercher(question: str, k: int = rag2.TOP_K, candidats: int = CANDIDATS):
    """Hybride pour présélectionner, puis reranker pour classer."""
    presel = rag2.chercher_hybride(question, k=candidats)
    if not presel:
        return []

    modele = charger()
    # Le cross-encoder bénéficie aussi des termes techniques : c'est lui
    # qui donnait 0,016 sur « tâche récurrente » et 0,779 sur « CronJob ».
    requete = expansion.etendre(question)
    paires = [(requete, c["texte"]) for c, _ in presel]
    scores = modele.predict(paires, show_progress_bar=False)

    ordre = sorted(range(len(presel)), key=lambda i: scores[i], reverse=True)[:k]
    return [(presel[i][0], float(scores[i])) for i in ordre]


if __name__ == "__main__":
    questions = (
        [" ".join(sys.argv[1:])]
        if len(sys.argv) > 1
        else [
            "Comment lire le state d'une autre configuration Terraform ?",
            "Quelles sont les conventions de nommage recommandées en Terraform ?",
            "Un pod est en CrashLoopBackOff avec l'exit code 137, que faire ?",
        ]
    )

    for q in questions:
        print(f"\n\033[1m QUESTION \033[0m {q}\n")
        print("  \033[2mhybride seul\033[0m")
        for c, s in rag2.chercher_hybride(q, k=3):
            print(f"    {s:.4f}  {c['source']}/{c['fichier']:<22} {c['titre'][:32]}")
        print("  \033[1mavec reranker\033[0m")
        for c, s in chercher(q, k=3):
            print(f"    {s:.4f}  {c['source']}/{c['fichier']:<22} {c['titre'][:32]}")
