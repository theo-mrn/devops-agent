"""RAG sur le corpus complet (387 chunks), via l'index pré-calculé.

Différences avec `rag.py` (brique 4) :
  - lit l'index sur disque au lieu de tout ré-encoder,
  - cite la PROVENANCE (source + fichier + section),
  - affiche l'écart de score, indice de la confiance du retrieval.

Usage :
    uv run python src/rag2.py "ta question"
    uv run python src/rag2.py            # questions de démonstration
"""

import os
import pickle
import sys
import threading
import time
from pathlib import Path

# Le modèle d'embedding est déjà en cache local : inutile d'interroger le
# Hub HuggingFace à chaque lancement. Sans cela, une requête réseau lente
# ou bloquée fige le démarrage.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import torch
from sentence_transformers import SentenceTransformer

# Import différé dans chercher() pour éviter une boucle : hybride
# importe rag2 pour la partie dense.

import config
import generateur

# Les modèles et paramètres vivent dans config.py, surchargeables par
# variables d'environnement. Ces alias gardent le code lisible.
MODELE_LLM = config.LLM
MODELE_EMB = config.EMBEDDING
INDEX = config.INDEX
TOP_K = config.TOP_K

SYSTEM = """Tu es un ingénieur SRE/DevOps expert.

RÈGLES ABSOLUES :

1. Réponds UNIQUEMENT à partir du CONTEXTE fourni ci-dessous.

2. N'utilise JAMAIS tes connaissances générales pour compléter le contexte.
   Avant de citer une commande ou un outil, vérifie qu'il apparaît
   littéralement dans le CONTEXTE. Sinon, ne le mentionne pas.

3. AVANT DE RÉPONDRE, vérifie que le contexte traite bien de l'OUTIL ou de la
   TECHNOLOGIE nommée dans la question. Une simple mention ne suffit pas : le
   contexte doit expliquer ce que la question demande.

   Exemples de contextes TROMPEURS :
   - question sur PostgreSQL, contexte sur le backend `pg` de Terraform → REFUSER
   - question sur Ansible, contexte sur les Deployments Kubernetes → REFUSER
   - question sur Python/Poetry, contexte sur le cache de dépendances CI → REFUSER
   - question sur une VERSION précise, contexte décrivant la fonctionnalité
     sans jamais donner de numéro de version → REFUSER

   Dans tous ces cas, réponds EXACTEMENT :
   "Le contexte fourni ne contient pas cette information."

   Cette règle s'applique même si tu connais la réponse par ailleurs.
   Ne devine jamais. Un refus honnête vaut mieux qu'une réponse inventée.

4. SÛRETÉ — commandes destructives (`delete --force`, `--grace-period=0`,
   `drain`, `terraform destroy`, `rm -rf`) :
   - jamais en première intention,
   - uniquement après avoir proposé les commandes d'inspection,
   - toujours précédées d'une réserve explicite indiquant qu'il s'agit d'un
     dernier recours et de ses conséquences,
   - jamais si la question ne porte pas sur une suppression.

5. Privilégie toujours l'inspection non-destructive : `describe`, `logs`,
   `get -o yaml`, `top`. Après un redémarrage de conteneur, `kubectl logs`
   exige le flag `--previous`.

6. Cite entre parenthèses le fichier source utilisé.
"""

QUESTIONS = [
    # Celle que le système ne pouvait PAS traiter avant l'élargissement.
    "Un pod est en CrashLoopBackOff avec l'exit code 137. Que s'est-il passé ?",
    "Quelle est la syntaxe du bloc moved en Terraform ?",
    "Comment configurer un readiness probe ?",
]

_cache = {}
# Le chargement doit être protégé : l'évaluateur appelle chercher() depuis
# plusieurs threads, et sans verrou chaque thread charge son propre modèle
# — trois copies de bge-m3 en mémoire, saturation immédiate.
_verrou = threading.Lock()


def charger():
    with _verrou:
        if "index" not in _cache:
            with INDEX.open("rb") as f:
                _cache["index"] = pickle.load(f)
            # Refuse un index encodé par un autre modèle : sans ce
            # contrôle, l'incohérence est silencieuse.
            config.verifier_index(_cache["index"].get("meta"))
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
    """Recherche hybride RRF : dense + BM25 fusionnés par rangs."""
    import hybride
    return [(c, s) for c, s, _ in hybride.chercher(question, k=k)]


def chercher_rerank(question: str, k: int = TOP_K):
    """Hybride puis reranking par cross-encoder — mode par défaut.

    Mesuré en brique 10 sur 1427 chunks : Hit Rate 97 % contre 94 % pour
    l'hybride seul. Le cross-encoder lit la question ET le document
    ensemble, là où dense et BM25 comparent des représentations calculées
    séparément.
    """
    import reranker
    return reranker.chercher(question, k=k)


def repondre(question: str) -> None:
    # Garde-fou de domaine : si la question nomme une technologie que le
    # corpus ne couvre pas, refuser sans appeler le modèle. Le retrieval
    # remonterait un contexte plausible mais trompeur (une question
    # PostgreSQL fait remonter le backend `pg` de Terraform).
    import expansion
    techno = expansion.hors_domaine(question)
    if techno:
        print(f"\n\033[1m QUESTION \033[0m {question}\n")
        print(f"\033[2mhors domaine : « {techno} » n'est pas couvert par le corpus\033[0m\n")
        print("Le contexte fourni ne contient pas cette information.")
        print("\033[2m" + "─" * 70 + "\033[0m")
        return

    debut = time.time()
    resultats = chercher_rerank(question)
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
    rep = generateur.generer(
        SYSTEM, f"CONTEXTE :\n\n{contexte}\n\n---\n\nQUESTION : {question}"
    )
    t_gen = time.time() - debut

    print(f"\n{rep.texte}")
    print(
        f"\n\033[2m→ recherche {t_recherche * 1000:.0f}ms · "
        f"contexte {rep.tokens_entree} tokens · "
        f"génération {t_gen:.1f}s · {generateur.modele_actif()}\033[0m"
    )
    print("\033[2m" + "─" * 70 + "\033[0m")


if __name__ == "__main__":
    questions = [" ".join(sys.argv[1:])] if len(sys.argv) > 1 else QUESTIONS
    for q in questions:
        repondre(q)
