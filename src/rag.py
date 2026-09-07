"""RAG complet : recherche → injection dans le prompt → génération.

C'est l'assemblage des briques 2 et 3 avec le modèle de la brique 1.

Le point délicat n'est pas la plomberie, c'est le PROMPT. Il doit :
  - interdire au modèle de puiser dans sa mémoire d'entraînement,
  - l'autoriser explicitement à dire que le contexte ne suffit pas,
  - lui demander de citer la section utilisée.

Sans ces trois règles, le modèle hallucine comme en baseline, mais avec
une source à l'appui — ce qui est pire.

Usage :
    uv run python src/rag.py
    uv run python src/rag.py "ma question"
"""

import sys
import time
from pathlib import Path

import ollama
import torch
from sentence_transformers import SentenceTransformer

import chunk_structure

MODELE_LLM = "qwen2.5-coder:7b-instruct-q4_K_M"
MODELE_EMB = "BAAI/bge-m3"
DOCUMENT = Path("data/raw/terraform-moved.mdx")
TOP_K = 3

SYSTEM = """Tu es un ingénieur SRE/DevOps expert.

RÈGLES ABSOLUES :
1. Réponds UNIQUEMENT à partir du CONTEXTE fourni ci-dessous.
2. N'utilise JAMAIS tes connaissances générales pour compléter le contexte.
3. Si le contexte ne contient pas la réponse, réponds exactement :
   "Le contexte fourni ne contient pas cette information."
   Ne devine pas. Ne propose pas de réponse approximative.
4. Cite entre parenthèses le titre de la section utilisée.
"""

# Les 3 questions de la baseline qui portent sur ce document, plus un
# contrôle négatif : une question hors sujet, dont la réponse n'est
# certainement pas dans le corpus.
QUESTIONS = [
    "Quelle est la syntaxe exacte du bloc moved en Terraform ?",
    "Est-ce que les arguments from et to prennent des guillemets ?",
    "Depuis quelle version de Terraform le bloc moved est-il disponible ?",
    "Comment configurer un readiness probe dans Kubernetes ?",
]


def construire_contexte(chunks: list[dict], indices: list[int]) -> str:
    """Assemble les chunks retenus, chacun étiqueté par sa section."""
    morceaux = []
    for i in indices:
        morceaux.append(f"[Section : {chunks[i]['titre']}]\n{chunks[i]['texte']}")
    return "\n\n---\n\n".join(morceaux)


def repondre(question: str, chunks: list[dict], vecteurs, encodeur) -> None:
    # 1. Recherche
    vec_q = encodeur.encode(question, normalize_embeddings=True)
    scores = vecteurs @ vec_q
    classement = sorted(range(len(chunks)), key=lambda i: scores[i], reverse=True)
    retenus = classement[:TOP_K]

    print(f"\n\033[1m QUESTION \033[0m {question}\n")
    print("\033[2mChunks retenus :\033[0m")
    for rang, i in enumerate(retenus):
        print(f"\033[2m  {rang + 1}. {scores[i]:.3f}  {chunks[i]['titre']}\033[0m")

    # 2. Injection
    contexte = construire_contexte(chunks, retenus)
    prompt = f"CONTEXTE :\n\n{contexte}\n\n---\n\nQUESTION : {question}"

    # 3. Génération
    debut = time.time()
    reponse = ollama.chat(
        model=MODELE_LLM,
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt},
        ],
        options={"temperature": 0.1},  # très bas : on veut de la fidélité
    )
    duree = time.time() - debut

    print(f"\n{reponse['message']['content']}")

    tokens_entree = reponse.get("prompt_eval_count", 0)
    tokens_sortie = reponse.get("eval_count", 0)
    print(
        f"\n\033[2m→ contexte {tokens_entree} tokens · "
        f"réponse {tokens_sortie} tokens · {duree:.1f}s\033[0m"
    )
    print("\033[2m" + "─" * 70 + "\033[0m")


def main() -> None:
    chunks = chunk_structure.decouper(DOCUMENT.read_text())

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    encodeur = SentenceTransformer(MODELE_EMB, device=device)
    vecteurs = encodeur.encode(
        [c["texte"] for c in chunks], normalize_embeddings=True
    )

    questions = [" ".join(sys.argv[1:])] if len(sys.argv) > 1 else QUESTIONS
    for q in questions:
        repondre(q, chunks, vecteurs, encodeur)


if __name__ == "__main__":
    main()
