"""Couche de génération : masque le fournisseur du LLM.

Le reste du pipeline appelle `generer(system, prompt)` sans savoir si la
réponse vient d'un modèle local (Ollama) ou d'une API (Anthropic).

    RAG_PROVIDER=ollama     (défaut) modèle local, gratuit, hors ligne
    RAG_PROVIDER=anthropic  API Claude, nécessite ANTHROPIC_API_KEY

Seule la GÉNÉRATION change de fournisseur. Le retrieval (embeddings,
BM25, reranker) reste local dans tous les cas : le corpus n'est jamais
envoyé en entier. En revanche, avec une API, les chunks retenus partent
dans le prompt à chaque requête — à peser si la documentation devient
confidentielle.
"""

import os

import devops_agent.core.config as config


class Reponse:
    """Réponse normalisée, quel que soit le fournisseur."""

    def __init__(self, texte: str, tokens_entree: int = 0, tokens_sortie: int = 0):
        self.texte = texte
        self.tokens_entree = tokens_entree
        self.tokens_sortie = tokens_sortie


def _generer_ollama(system: str, prompt: str) -> Reponse:
    import ollama

    rep = ollama.Client(timeout=config.TIMEOUT_LLM).chat(
        model=config.LLM,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        options={"temperature": config.TEMPERATURE},
    )
    return Reponse(
        rep["message"]["content"],
        rep.get("prompt_eval_count", 0),
        rep.get("eval_count", 0),
    )


def _generer_anthropic(system: str, prompt: str) -> Reponse:
    import anthropic

    client = anthropic.Anthropic(timeout=float(config.TIMEOUT_LLM))

    # Le system prompt est mis en cache : il est identique à chaque requête
    # et représente ~400 tokens. Les lectures suivantes coûtent ~10 % du prix.
    rep = client.messages.create(
        model=config.LLM_API,
        max_tokens=config.MAX_TOKENS_API,
        system=[{
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": prompt}],
    )

    # Un refus de sécurité laisse `content` vide : le vérifier avant de lire.
    if rep.stop_reason == "refusal":
        motif = getattr(rep.stop_details, "category", "non précisé")
        return Reponse(f"[refus du modèle : {motif}]",
                       rep.usage.input_tokens, rep.usage.output_tokens)

    texte = "".join(b.text for b in rep.content if b.type == "text")
    return Reponse(texte, rep.usage.input_tokens, rep.usage.output_tokens)


def generer(system: str, prompt: str) -> Reponse:
    """Génère une réponse avec le fournisseur configuré."""
    if config.PROVIDER == "anthropic":
        return _generer_anthropic(system, prompt)
    if config.PROVIDER == "ollama":
        return _generer_ollama(system, prompt)
    raise ValueError(
        f"RAG_PROVIDER='{config.PROVIDER}' inconnu — attendu « ollama » ou « anthropic »"
    )


def modele_actif() -> str:
    """Nom du modèle réellement utilisé, pour l'affichage."""
    return config.LLM_API if config.PROVIDER == "anthropic" else config.LLM


def verifier_disponible() -> str | None:
    """Message d'erreur si le fournisseur n'est pas utilisable, sinon None."""
    if config.PROVIDER == "anthropic":
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return "le paquet `anthropic` n'est pas installé — `uv add anthropic`"
        if not os.environ.get("ANTHROPIC_API_KEY"):
            # Le SDK sait aussi lire un profil `ant auth login` : on ne
            # bloque pas, on signale seulement.
            return None
    return None
