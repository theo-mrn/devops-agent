"""Baseline : interroger le modèle SANS aucune aide externe.

Ce script sert de point de référence. Toutes les questions posées ici
seront reposées plus tard avec le RAG branché, pour mesurer ce que
la recherche documentaire apporte réellement.

Usage :
    uv run python src/baseline.py
    uv run python src/baseline.py "ma propre question"
"""

import sys
import time

import ollama

MODELE = "qwen2.5-coder:7b-instruct-q4_K_M"

SYSTEM = (
    "Tu es un ingénieur SRE/DevOps expert. "
    "Tu privilégies toujours les commandes d'inspection non-destructives "
    "avant de proposer un correctif. "
    "Si tu n'es pas certain d'une information, tu le dis explicitement."
)

# Questions de référence, choisies pour couvrir trois régimes différents.
QUESTIONS = [
    # 1. Connaissance stable : le modèle devrait savoir, RAG ou pas.
    "Un pod Kubernetes est en CrashLoopBackOff avec un exit code 137. "
    "Quelle est la cause la plus probable et comment le vérifier ?",

    # 2. Syntaxe précise : teste la rigueur, terrain typique des hallucinations.
    "Écris un manifest Kubernetes minimal pour un Deployment nginx "
    "avec des resource limits et un readiness probe.",

    # 3. Spécifique/récent : le modèle ne peut PAS savoir. C'est ici que
    #    le RAG fera la différence, et ici qu'on observe s'il invente.
    "Quelle est la syntaxe exacte du bloc `moved` en Terraform "
    "et depuis quelle version est-il disponible ?",
]


def demander(question: str) -> None:
    print(f"\n\033[1m QUESTION \033[0m {question}\n")

    debut = time.time()
    reponse = ollama.chat(
        model=MODELE,
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": question},
        ],
        options={"temperature": 0.2},  # bas = déterministe, adapté au technique
    )
    duree = time.time() - debut

    print(reponse["message"]["content"])

    # Les métriques d'Ollama sont en nanosecondes.
    tokens = reponse.get("eval_count", 0)
    vitesse = tokens / (reponse.get("eval_duration", 1) / 1e9)
    print(f"\n\033[2m→ {tokens} tokens en {duree:.1f}s ({vitesse:.1f} tok/s)\033[0m")
    print("\033[2m" + "─" * 70 + "\033[0m")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        demander(" ".join(sys.argv[1:]))
    else:
        for q in QUESTIONS:
            demander(q)
