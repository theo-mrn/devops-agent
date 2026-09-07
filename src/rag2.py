"""RAG sur le corpus complet (387 chunks), via l'index pré-calculé.

Différences avec `rag.py` (brique 4) :
  - lit l'index sur disque au lieu de tout ré-encoder,
  - cite la PROVENANCE (source + fichier + section),
  - affiche l'écart de score, indice de la confiance du retrieval.

Usage :
    uv run python src/rag2.py "ta question"
    uv run python src/rag2.py            # questions de démonstration
"""

import pickle
import sys
import time
from pathlib import Path

import ollama
import torch
from sentence_transformers import SentenceTransformer

# Import différé dans chercher() pour éviter une boucle : hybride
# importe rag2 pour la partie dense.

MODELE_LLM = "qwen2.5-coder:7b-instruct-q4_K_M"
MODELE_EMB = "BAAI/bge-m3"
INDEX = Path("data/index/corpus.pkl")
TOP_K = 3

SYSTEM = """Tu es un ingénieur SRE/DevOps expert.

RÈGLES ABSOLUES :
1. Réponds UNIQUEMENT à partir du CONTEXTE fourni ci-dessous.
2. N'utilise JAMAIS tes connaissances générales pour compléter le contexte.
3. Si le contexte ne contient pas la réponse, réponds exactement :
   "Le contexte fourni ne contient pas cette information."
   Ne devine pas. Ne propose pas de réponse approximative.
4. Cite entre parenthèses le fichier source utilisé.
"""

QUESTIONS = [
    # Celle que le système ne pouvait PAS traiter avant l'élargissement.
    "Un pod est en CrashLoopBackOff avec l'exit code 137. Que s'est-il passé ?",
    "Quelle est la syntaxe du bloc moved en Terraform ?",
    "Comment configurer un readiness probe ?",
]

_cache = {}


def charger():
    if "index" not in _cache:
        with INDEX.open("rb") as f:
            _cache["index"] = pickle.load(f)
        device = "mps" if torch.backends.mps.is_available() else "cpu"
        _cache["enc"] = SentenceTransformer(MODELE_EMB, device=device)
    return _cache["index"], _cache["enc"]


def chercher_dense(question: str, k: int = TOP_K):
    """Recherche vectorielle pure."""
    index, enc = charger()
    vq = enc.encode(question, normalize_embeddings=True)
    scores = index["vecteurs"] @ vq
    ordre = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]
    return [(index["chunks"][i], float(scores[i])) for i in ordre]


# Alias conservé : hybride.py appelle rag2.chercher pour la partie dense.
chercher = chercher_dense


def chercher_hybride(question: str, k: int = TOP_K):
    """Recherche hybride RRF — le mode par défaut du pipeline.

    Mesuré en brique 7 : Hit Rate 100 % / MRR 0,933,
    contre 90 % / 0,850 pour le dense seul.
    """
    import hybride
    return [(c, s) for c, s, _ in hybride.chercher(question, k=k)]


def repondre(question: str) -> None:
    debut = time.time()
    resultats = chercher_hybride(question)
    t_recherche = time.time() - debut

    print(f"\n\033[1m QUESTION \033[0m {question}\n")
    print("\033[2mSources retenues :\033[0m")
    for c, s in resultats:
        print(f"\033[2m  {s:.3f}  {c['source']}/{c['fichier']} — {c['titre'][:45]}\033[0m")

    # Écart entre le 1er et le dernier retenu : un écart large signale un
    # retrieval confiant, un écart nul que rien ne se détache vraiment.
    ecart = resultats[0][1] - resultats[-1][1]
    print(f"\033[2m  écart 1er↔{TOP_K}e : {ecart:.3f}\033[0m")

    contexte = "\n\n---\n\n".join(
        f"[Source : {c['source']}/{c['fichier']} — {c['titre']}]\n{c['texte']}"
        for c, _ in resultats
    )

    debut = time.time()
    rep = ollama.chat(
        model=MODELE_LLM,
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"CONTEXTE :\n\n{contexte}\n\n---\n\nQUESTION : {question}"},
        ],
        options={"temperature": 0.1},
    )
    t_gen = time.time() - debut

    print(f"\n{rep['message']['content']}")
    print(
        f"\n\033[2m→ recherche {t_recherche * 1000:.0f}ms · "
        f"contexte {rep.get('prompt_eval_count', 0)} tokens · "
        f"génération {t_gen:.1f}s\033[0m"
    )
    print("\033[2m" + "─" * 70 + "\033[0m")


if __name__ == "__main__":
    questions = [" ".join(sys.argv[1:])] if len(sys.argv) > 1 else QUESTIONS
    for q in questions:
        repondre(q)
