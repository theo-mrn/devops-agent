"""Recherche hybride : fusion du dense et de BM25 par RRF.

Pourquoi RRF (Reciprocal Rank Fusion) plutôt qu'une somme pondérée
des scores : les scores BM25 (0 à 15, non bornés) et cosinus (0 à 1)
ne sont pas comparables. Les RANGS, eux, le sont.

    score_RRF(doc) = Σ  poids_méthode / (k + rang_dans_cette_méthode)

k=60 est la valeur de la littérature : elle amortit l'écart entre les
premiers rangs sans écraser la queue de liste.

Usage :
    uv run python src/fusion.py "ta question"
    uv run python src/fusion.py --comparer   # dense vs bm25 vs hybride
"""

import sys

import devops_agent.retrieval.lexical as bm25_mod
import devops_agent.retrieval.expansion as expansion
import devops_agent.retrieval.pipeline as pipeline

import devops_agent.core.config as config

K_RRF = config.K_RRF
POIDS_DENSE = 1.0
POIDS_BM25 = 1.0
PROFONDEUR = config.CANDIDATS_RERANK


def chercher(question: str, k: int = 3, poids_dense: float = POIDS_DENSE,
             poids_bm25: float = POIDS_BM25):
    """Fusionne les deux classements par RRF."""
    # L'expansion ne s'applique qu'à la RECHERCHE : le prompt envoyé au
    # modèle garde la question d'origine, sinon elle devient illisible.
    requete = expansion.etendre(question)
    dense = pipeline.chercher(requete, k=PROFONDEUR)
    lexical = bm25_mod.chercher(requete, k=PROFONDEUR)

    # Clé d'identité d'un chunk : son texte suffit et évite de dépendre
    # d'un index positionnel qui diffère entre les deux méthodes.
    scores: dict[str, float] = {}
    chunks: dict[str, dict] = {}
    details: dict[str, dict] = {}

    for rang, (chunk, _) in enumerate(dense, start=1):
        cle = chunk["texte"]
        scores[cle] = scores.get(cle, 0.0) + poids_dense / (K_RRF + rang)
        chunks[cle] = chunk
        details.setdefault(cle, {})["dense"] = rang

    for rang, (chunk, _) in enumerate(lexical, start=1):
        cle = chunk["texte"]
        scores[cle] = scores.get(cle, 0.0) + poids_bm25 / (K_RRF + rang)
        chunks[cle] = chunk
        details.setdefault(cle, {})["bm25"] = rang

    ordre = sorted(scores, key=lambda c: scores[c], reverse=True)[:k]
    return [(chunks[c], scores[c], details[c]) for c in ordre]


def comparer(question: str) -> None:
    print(f"\n\033[1m QUESTION \033[0m {question}\n")

    def ligne(chunk, extra=""):
        return f"    {chunk['source']}/{chunk['fichier']} — {chunk['titre'][:40]}{extra}"

    print("  \033[1mdense\033[0m")
    for c, s in pipeline.chercher(question, k=3):
        print(ligne(c, f"  \033[2m{s:.3f}\033[0m"))

    print("  \033[1mBM25\033[0m")
    for c, s in bm25_mod.chercher(question, k=3):
        print(ligne(c, f"  \033[2m{s:.2f}\033[0m"))

    print("  \033[1mhybride (RRF)\033[0m")
    for c, s, d in chercher(question, k=3):
        origine = " ".join(f"{m}#{r}" for m, r in sorted(d.items()))
        print(ligne(c, f"  \033[2m{s:.4f}  [{origine}]\033[0m"))


if __name__ == "__main__":
    if "--comparer" in sys.argv or len(sys.argv) == 1:
        for q in [
            "Terraform détruit-il la ressource quand on utilise le bloc moved ?",
            "Que signifie un exit code 137 ?",
            "Comment configurer un readiness probe ?",
        ]:
            comparer(q)
    else:
        comparer(" ".join(sys.argv[1:]))
