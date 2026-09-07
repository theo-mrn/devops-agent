"""Première vraie recherche sémantique sur les chunks du document.

On indexe les 5 chunks produits par le découpage structurel, puis on
pose des questions et on regarde ce qui remonte — et dans quel ordre.

Usage :
    uv run python src/recherche.py
    uv run python src/recherche.py "ma question"
"""

import sys
from pathlib import Path

import torch
from sentence_transformers import SentenceTransformer

import chunk_structure

MODELE = "BAAI/bge-m3"
DOCUMENT = Path("data/raw/terraform-moved.mdx")

QUESTIONS = [
    "Quelle est la syntaxe du bloc moved ?",
    "Est-ce que from prend des guillemets ?",
    "Depuis quelle version de Terraform le bloc moved existe-t-il ?",
]


def main() -> None:
    chunks = chunk_structure.decouper(DOCUMENT.read_text())

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    modele = SentenceTransformer(MODELE, device=device)

    # bge-m3 recommande de ne PAS préfixer les documents, mais l'ordre
    # importe : on encode une fois les chunks, puis chaque question.
    vec_chunks = modele.encode(
        [c["texte"] for c in chunks], normalize_embeddings=True
    )

    questions = [" ".join(sys.argv[1:])] if len(sys.argv) > 1 else QUESTIONS

    for question in questions:
        vec_q = modele.encode(question, normalize_embeddings=True)
        scores = vec_chunks @ vec_q

        classement = sorted(
            range(len(chunks)), key=lambda i: scores[i], reverse=True
        )

        print(f"\n\033[1m QUESTION \033[0m {question}\n")
        for rang, i in enumerate(classement):
            marque = "\033[32m→\033[0m" if rang == 0 else " "
            titre = chunks[i]["titre"]
            print(f" {marque} {scores[i]:.3f}  \033[1m{titre}\033[0m")

        # Le meilleur chunk, en entier : c'est lui qui partira au modèle.
        meilleur = chunks[classement[0]]
        print(f"\n\033[2m--- contenu du chunk retenu ---\033[0m")
        extrait = meilleur["texte"]
        print(extrait[:400] + ("..." if len(extrait) > 400 else ""))
        print("\033[2m" + "─" * 70 + "\033[0m")


if __name__ == "__main__":
    main()
