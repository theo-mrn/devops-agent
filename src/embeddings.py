"""Embeddings : transformer du texte en vecteurs, et mesurer la similarité.

Question à laquelle ce script répond :
    comment une machine sait-elle que « exit code 137 » et « OOMKilled »
    parlent de la même chose, alors qu'ils n'ont aucun mot en commun ?

Usage :
    uv run python src/embeddings.py
"""

import time

import torch
from sentence_transformers import SentenceTransformer

MODELE = "BAAI/bge-m3"

# Phrases choisies pour tester trois cas de figure :
#   - même sens, mots différents   → la similarité doit être ÉLEVÉE
#   - mots communs, sens différent → la similarité doit être BASSE
#   - aucun rapport                → plancher de référence
PHRASES = [
    "Un pod est tué avec l'exit code 137",           # 0
    "Le conteneur a été OOMKilled par manque de mémoire",  # 1
    "Le pod ne démarre pas : ImagePullBackOff",      # 2
    "Comment déplacer une ressource Terraform",      # 3
    "Le bloc moved change l'adresse d'une ressource", # 4
    "La recette de la tarte aux pommes",             # 5
]


def charger() -> SentenceTransformer:
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"Chargement de {MODELE} sur {device}...")
    debut = time.time()
    modele = SentenceTransformer(MODELE, device=device)
    print(f"→ chargé en {time.time() - debut:.1f}s\n")
    return modele


def matrice_similarite(modele: SentenceTransformer, phrases: list[str]) -> None:
    debut = time.time()
    vecteurs = modele.encode(phrases, normalize_embeddings=True)
    duree = time.time() - debut

    print(f"Dimensions d'un vecteur : {vecteurs.shape[1]}")
    print(f"{len(phrases)} phrases encodées en {duree:.2f}s\n")

    # Aperçu concret de ce qu'est un embedding.
    print("Les 8 premières valeurs du vecteur de la phrase 0 :")
    print(f"  {vecteurs[0][:8].round(4)}")
    print(f"  (... et {vecteurs.shape[1] - 8} autres)\n")

    # Vecteurs normalisés → le produit scalaire EST la similarité cosinus.
    sims = vecteurs @ vecteurs.T

    print("Matrice de similarité (1.00 = identique) :\n")
    print("      " + "".join(f"{i:>7}" for i in range(len(phrases))))
    for i, ligne in enumerate(sims):
        cellules = ""
        for j, v in enumerate(ligne):
            if i == j:
                cellules += f"\033[2m{v:>7.2f}\033[0m"
            elif v > 0.6:
                cellules += f"\033[32m{v:>7.2f}\033[0m"   # proche
            elif v < 0.35:
                cellules += f"\033[31m{v:>7.2f}\033[0m"   # éloigné
            else:
                cellules += f"{v:>7.2f}"
        print(f"  {i} :{cellules}")

    print("\nLégende des phrases :")
    for i, p in enumerate(phrases):
        print(f"  {i} — {p}")

    print("\n\033[1mLes paires qui comptent :\033[0m")
    for a, b, attendu in [
        (0, 1, "ÉLEVÉE — même sens, zéro mot commun"),
        (3, 4, "ÉLEVÉE — même sujet, formulations différentes"),
        (0, 2, "moyenne — tous deux des pannes de pod"),
        (0, 5, "BASSE — aucun rapport"),
    ]:
        print(f"  {a}↔{b} : \033[1m{sims[a][b]:.3f}\033[0m   ({attendu})")


if __name__ == "__main__":
    modele = charger()
    matrice_similarite(modele, PHRASES)
