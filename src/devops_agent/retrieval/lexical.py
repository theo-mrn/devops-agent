"""BM25 : recherche par mots-clés exacts.

Complémentaire de la recherche dense. Là où l'embedding capte le SENS
(et rate les termes rares), BM25 note selon la RARETÉ des mots partagés :
un terme comme « OOMKilled » ou « moved » pèse beaucoup plus qu'un mot
courant comme « resource » ou « pod ».

La tokenisation est le point délicat en DevOps : `ImagePullBackOff`,
`aws_instance.a` ou `--previous` ne se découpent pas comme du texte
ordinaire.

Usage :
    uv run python src/lexical.py "ta question"
"""

import pickle
import re
import sys
import threading
import unicodedata
from pathlib import Path

from rank_bm25 import BM25Okapi

import devops_agent.core.config as config

INDEX = config.INDEX

# Mots vides : trop fréquents pour discriminer quoi que ce soit.
VIDES = {
    "le", "la", "les", "un", "une", "des", "de", "du", "et", "ou", "a", "à",
    "est", "sont", "que", "qui", "quoi", "comment", "pourquoi", "quel", "quelle",
    "pour", "dans", "sur", "avec", "sans", "en", "au", "aux", "ce", "cette",
    "the", "of", "to", "in", "is", "are", "for", "on", "with", "and", "or",
    "a", "an", "it", "this", "that", "can", "you", "how", "what", "when",
}


def tokeniser(texte: str) -> list[str]:
    """Découpe adaptée au vocabulaire DevOps.

    Conserve les identifiants techniques entiers (`aws_instance`,
    `OOMKilled`) ET produit leurs sous-mots, pour qu'une requête
    « image pull » retrouve `ImagePullBackOff`.
    """
    # Retire les accents AVANT de découper : sans cela « détruit-il »
    # produit « truit-il », la regex s'arrêtant sur le caractère accentué.
    texte = unicodedata.normalize("NFD", texte.lower())
    texte = "".join(c for c in texte if unicodedata.category(c) != "Mn")

    # Mots, y compris ceux contenant _ - . / :
    bruts = re.findall(r"[a-z0-9][a-z0-9_\-./:]*", texte)

    tokens: list[str] = []
    for mot in bruts:
        if mot in VIDES or len(mot) < 2:
            continue
        tokens.append(mot)

        # Décompose les identifiants composés : aws_instance.a → aws, instance, a
        parties = [p for p in re.split(r"[_\-./:]+", mot) if len(p) >= 2]
        if len(parties) > 1:
            tokens.extend(p for p in parties if p not in VIDES)

    return tokens


def tokeniser_casse(texte: str) -> list[str]:
    """Ajoute la décomposition du CamelCase : ImagePullBackOff →
    image, pull, back, off. Appliqué au texte AVANT mise en minuscules."""
    tokens = tokeniser(texte)
    for mot in re.findall(r"[A-Za-z][A-Za-z0-9]*", texte):
        parties = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])", mot)
        if len(parties) > 1:
            tokens.extend(p.lower() for p in parties if len(p) >= 2 and p.lower() not in VIDES)
    return tokens


_cache = {}
_verrou = threading.Lock()


def charger():
    with _verrou:
        if "bm25" not in _cache:
            with INDEX.open("rb") as f:
                index = pickle.load(f)
            corpus_tokens = [tokeniser_casse(c["texte"]) for c in index["chunks"]]
            _cache["bm25"] = BM25Okapi(corpus_tokens)
            _cache["chunks"] = index["chunks"]
    return _cache["bm25"], _cache["chunks"]


def chercher(question: str, k: int = 3):
    moteur, chunks = charger()
    scores = moteur.get_scores(tokeniser_casse(question))
    ordre = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]
    return [(chunks[i], float(scores[i])) for i in ordre]


if __name__ == "__main__":
    questions = (
        [" ".join(sys.argv[1:])]
        if len(sys.argv) > 1
        else [
            "Terraform détruit-il la ressource quand on utilise le bloc moved ?",
            "Que signifie un exit code 137 ?",
            "Qu'est-ce qu'un OOMKilled ?",
        ]
    )

    for q in questions:
        print(f"\n\033[1m QUESTION \033[0m {q}")
        print(f"\033[2mtokens : {tokeniser_casse(q)[:14]}\033[0m\n")
        for c, s in chercher(q):
            print(f"  {s:6.2f}  {c['source']}/{c['fichier']} — {c['titre'][:45]}")
