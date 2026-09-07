"""Ablation : mesurer l'effet d'un changement sur le score.

Ici : que se passe-t-il si on remplace le chunking structurel par le
chunking naïf de la brique 2 ? C'est le premier vrai usage de la
brique 5 — transformer une intuition en mesure.
"""

from pathlib import Path

import torch
import yaml
from sentence_transformers import SentenceTransformer

import chunk_naif
import chunk_structure
import rag

TEXTE = Path(rag.DOCUMENT).read_text()
CAS = yaml.safe_load(Path("eval/questions.yaml").read_text())

device = "mps" if torch.backends.mps.is_available() else "cpu"
enc = SentenceTransformer(rag.MODELE_EMB, device=device)


def mesurer(nom: str, textes: list[str], contient_reponse) -> None:
    """contient_reponse(chunk) -> bool : le chunk répond-il au cas ?"""
    vecs = enc.encode(textes, normalize_embeddings=True)

    hits, rrs, applicables = 0, 0.0, 0
    for cas in CAS:
        if not cas.get("chunk_attendu"):
            continue  # cas "absent" : pas de retrieval à évaluer
        applicables += 1

        vq = enc.encode(cas["question"], normalize_embeddings=True)
        scores = vecs @ vq
        ordre = sorted(range(len(textes)), key=lambda i: scores[i], reverse=True)

        rang = None
        for r, i in enumerate(ordre[: rag.TOP_K]):
            if contient_reponse(textes[i], cas):
                rang = r + 1
                break

        if rang:
            hits += 1
            rrs += 1.0 / rang

    print(f"  {nom:<22} Hit Rate {hits / applicables:>5.0%}   MRR {rrs / applicables:.3f}   ({len(textes)} chunks)")


# Pour comparer équitablement deux découpages différents, on ne peut pas
# se fier aux titres de section (le naïf n'en a pas). On vérifie donc si
# le chunk contient bien les termes attendus dans la réponse.
def repond(chunk: str, cas: dict) -> bool:
    for attendu in cas.get("doit_contenir", []):
        alternatives = attendu if isinstance(attendu, list) else [attendu]
        if not any(a.lower() in chunk.lower() for a in alternatives):
            return False
    return True


print("\n\033[1m ABLATION — effet du chunking sur le retrieval \033[0m\n")

mesurer(
    "structurel",
    [c["texte"] for c in chunk_structure.decouper(TEXTE)],
    repond,
)
mesurer(
    "naïf (500 car.)",
    chunk_naif.decouper(TEXTE, 500, 50),
    repond,
)
mesurer(
    "naïf (200 car.)",
    chunk_naif.decouper(TEXTE, 200, 20),
    repond,
)
mesurer(
    "naïf (1000 car.)",
    chunk_naif.decouper(TEXTE, 1000, 100),
    repond,
)
print()
