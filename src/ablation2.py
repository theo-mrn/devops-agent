"""Ablation : dense vs BM25 vs hybride, sur le jeu d'évaluation.

Mesure le retrieval uniquement (pas de LLM) : rapide, et c'est là que
se joue l'effet du changement.
"""

import sys
from pathlib import Path

import yaml

import bm25 as bm25_mod
import hybride
import rag2

CAS = [c for c in yaml.safe_load(Path("eval/questions.yaml").read_text())
       if c.get("fichier_attendu")]


def mesurer(nom: str, chercher, k: int = 3) -> tuple[float, float]:
    hits, rrs = 0, 0.0
    echecs = []
    for cas in CAS:
        resultats = chercher(cas["question"], k=k)
        fichiers = [r[0]["fichier"] for r in resultats]
        rang = next((i + 1 for i, f in enumerate(fichiers) if f in cas["fichier_attendu"]), None)
        if rang:
            hits += 1
            rrs += 1.0 / rang
        else:
            echecs.append(cas["id"])

    hr, mrr = hits / len(CAS), rrs / len(CAS)
    detail = f"  \033[2m({', '.join(echecs)})\033[0m" if echecs else ""
    print(f"  {nom:<28} Hit Rate {hr:>5.0%}   MRR {mrr:.3f}{detail}")
    return hr, mrr


print(f"\n\033[1m ABLATION — méthodes de recherche \033[0m  ({len(CAS)} cas, top-3)\n")

mesurer("dense (bge-m3)", rag2.chercher)
mesurer("BM25 seul", bm25_mod.chercher)
mesurer("hybride RRF 1:1", hybride.chercher)

print()
for pd, pb in [(2.0, 1.0), (1.0, 2.0), (3.0, 1.0), (1.0, 3.0)]:
    mesurer(
        f"hybride dense×{pd:.0f} bm25×{pb:.0f}",
        lambda q, k=3, pd=pd, pb=pb: hybride.chercher(q, k=k, poids_dense=pd, poids_bm25=pb),
    )
print()
