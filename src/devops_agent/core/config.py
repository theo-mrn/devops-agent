"""Configuration centrale : modèles et paramètres du pipeline.

Chaque valeur est surchargeable par variable d'environnement, ce qui
permet de tester un modèle sans toucher au code :

    RAG_LLM=llama3.1:8b make ask Q="..."
    RAG_EMB=BAAI/bge-large-en-v1.5 make index

⚠️ Changer RAG_EMB oblige à ré-encoder tout le corpus (`make index`) :
l'index contient des vecteurs propres au modèle d'embedding. Un index
encodé par un modèle et interrogé par un autre produit des résultats
absurdes SANS lever d'erreur — d'où la vérification de `verifier_index()`,
appelée au chargement.

Changer RAG_LLM ou RAG_RERANKER ne demande aucune ré-indexation.
"""

import os
from pathlib import Path


def _charger_env() -> None:
    """Lit le fichier .env à la racine, sans écraser l'environnement.

    Une variable déjà définie dans le shell l'emporte : c'est la
    convention habituelle, et elle permet de surcharger ponctuellement
    sans éditer le fichier.

    Écrit à la main plutôt qu'avec python-dotenv : une dépendance de moins
    pour une vingtaine de lignes.
    """
    fichier = Path(__file__).resolve().parents[3] / ".env"
    if not fichier.exists():
        return

    for ligne in fichier.read_text().splitlines():
        ligne = ligne.strip()
        if not ligne or ligne.startswith("#") or "=" not in ligne:
            continue
        cle, _, valeur = ligne.partition("=")
        cle = cle.strip()
        valeur = valeur.strip().strip("\"'")
        if cle and cle not in os.environ:
            os.environ[cle] = valeur


_charger_env()

# ── Fournisseur de génération ────────────────────────────────────

# « ollama » : modèle local, gratuit, hors ligne.
# « anthropic » : API Claude, nécessite ANTHROPIC_API_KEY.
#
# Seule la génération change : le retrieval (embeddings, BM25, reranker)
# reste local dans les deux cas. Avec une API, les chunks retenus partent
# néanmoins dans le prompt à chaque requête.
PROVIDER = os.environ.get("RAG_PROVIDER", "ollama")

# ── Modèles ──────────────────────────────────────────────────────

# Génération locale. Tout modèle Ollama fonctionne (`ollama list`).
# Qwen2.5-Coder est retenu pour sa maîtrise de YAML/HCL/Bash.
LLM = os.environ.get("RAG_LLM", "qwen2.5-coder:7b-instruct-q4_K_M")

# Génération par API. Claude Opus 5 par défaut ; claude-sonnet-5 coûte
# 2,5x moins cher, claude-haiku-4-5 5x moins.
LLM_API = os.environ.get("RAG_LLM_API", "claude-opus-5")

# Plafond de sortie côté API. Les réponses du RAG font 100-600 tokens ;
# 4096 laisse de la marge sans risque de troncature.
MAX_TOKENS_API = int(os.environ.get("RAG_MAX_TOKENS", "4096"))

# Embeddings. bge-m3 est multilingue (le corpus est en anglais, les
# questions en français) et gère 8k de contexte.
# ⚠️ Un changement impose `make index`.
EMBEDDING = os.environ.get("RAG_EMB", "BAAI/bge-m3")

# Reranker (cross-encoder). Mesuré en brique 10 : Hit Rate 94 % → 97 %.
# Mettre la chaîne vide désactive le reranking.
RERANKER = os.environ.get("RAG_RERANKER", "BAAI/bge-reranker-v2-m3")

# ── Paramètres de recherche ──────────────────────────────────────

# Nombre de chunks envoyés au modèle. Mesuré en brique 9 : passer de 3 à 5
# fait monter le Hit Rate de 91 % à 97 %. Au-delà, aucun gain.
TOP_K = int(os.environ.get("RAG_TOP_K", "5"))

# Candidats présélectionnés avant reranking.
CANDIDATS_RERANK = int(os.environ.get("RAG_CANDIDATS", "20"))

# Fusion RRF : k=60 est la valeur de la littérature.
K_RRF = int(os.environ.get("RAG_K_RRF", "60"))

# ── Chunking ─────────────────────────────────────────────────────

TAILLE_MAX_CHUNK = int(os.environ.get("RAG_CHUNK_MAX", "2000"))
TAILLE_MIN_CHUNK = int(os.environ.get("RAG_CHUNK_MIN", "100"))

# ── Génération ───────────────────────────────────────────────────

TEMPERATURE = float(os.environ.get("RAG_TEMPERATURE", "0.1"))
TIMEOUT_LLM = int(os.environ.get("RAG_TIMEOUT", "180"))

# ── Chemins ──────────────────────────────────────────────────────

# core/ → devops_agent/ → src/ → racine du projet
RACINE = Path(__file__).resolve().parents[3]
SOURCES = [RACINE / "data/raw", RACINE / "data/interne"]
INDEX = RACINE / "data/index/corpus.pkl"
CAS_TEST = RACINE / "eval/questions.yaml"


def verifier_index(meta: dict | None) -> None:
    """Refuse un index encodé par un autre modèle d'embedding.

    Sans ce contrôle, l'incohérence est silencieuse : les vecteurs de
    l'index et ceux des requêtes vivent dans des espaces différents, la
    recherche renvoie n'importe quoi, et rien ne signale l'erreur.
    """
    if meta is None:
        return  # index d'avant l'introduction des métadonnées
    encode_par = meta.get("embedding")
    if encode_par and encode_par != EMBEDDING:
        raise RuntimeError(
            f"\n  L'index a été encodé avec « {encode_par} »"
            f"\n  mais la configuration demande « {EMBEDDING} »."
            f"\n  Les vecteurs sont incompatibles."
            f"\n  → lance `make index` pour ré-encoder le corpus."
        )


def cle_api_disponible() -> bool:
    """Une clé Anthropic est-elle utilisable ?"""
    return bool(os.environ.get("ANTHROPIC_API_KEY")
                or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def resume() -> str:
    modele = LLM_API if PROVIDER == "anthropic" else LLM
    return (
        f"  fournisseur {PROVIDER}\n"
        f"  LLM        {modele}\n"
        f"  embedding  {EMBEDDING}\n"
        f"  reranker   {RERANKER or '(désactivé)'}\n"
        f"  top-k      {TOP_K}  ·  candidats {CANDIDATS_RERANK}  ·  T° {TEMPERATURE}\n"
        f"  clé API    {'présente' if cle_api_disponible() else 'absente'}"
    )


if __name__ == "__main__":
    print(f"\n\033[1m CONFIGURATION \033[0m\n")
    print(resume())
    if INDEX.exists():
        import pickle
        with INDEX.open("rb") as f:
            d = pickle.load(f)
        meta = d.get("meta")
        n = len(d["chunks"])
        print(f"\n  index      {n} chunks")
        if meta:
            print(f"             encodé par « {meta.get('embedding')} »")
            try:
                verifier_index(meta)
                print("             \033[32m✓ cohérent avec la configuration\033[0m")
            except RuntimeError as e:
                print(f"\033[31m{e}\033[0m")
        else:
            print("             \033[33m(pas de métadonnées — index antérieur)\033[0m")
    else:
        print("\n  index      \033[33mabsent — lance `make index`\033[0m")
    print()
