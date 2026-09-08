"""Indexation de manifests Kubernetes.

Le chunking par section markdown ne convient pas au YAML : un manifest
n'a pas de titres, mais des documents séparés par `---`, chacun décrivant
une ressource complète.

Un chunk = une ressource. C'est l'unité de sens naturelle : couper un
Deployment en deux le rend inexploitable, et regrouper trois ressources
dilue la recherche.

Chaque chunk reçoit un en-tête lisible qui porte ce qu'une recherche
sémantique doit trouver :

    [Deployment n8n/n8n · manifest kubernetes/system/n8n/deployment.yml]

Sans cet en-tête, un `spec:` isolé ne dit ni de quoi il parle ni où il
vit.
"""

import os
import re
from pathlib import Path

import yaml

# Les champs qui n'apportent rien au diagnostic et polluent l'embedding.
CHAMPS_INUTILES = {
    "creationTimestamp", "resourceVersion", "uid", "generation",
    "managedFields", "selfLink", "status",
}

# Un manifest scellé ne contient que du chiffré : l'indexer noierait
# l'index sous des chaînes aléatoires.
KINDS_IGNORES = {"SealedSecret", "Secret"}

TAILLE_MAX = int(os.environ.get("RAG_MANIFEST_MAX", "4000"))


def _nettoyer(objet):
    """Retire récursivement les champs de métadonnées sans valeur."""
    if isinstance(objet, dict):
        return {
            cle: _nettoyer(valeur)
            for cle, valeur in objet.items()
            if cle not in CHAMPS_INUTILES
        }
    if isinstance(objet, list):
        return [_nettoyer(element) for element in objet]
    return objet


def _identite(ressource: dict) -> tuple[str, str, str]:
    """(kind, namespace, nom) d'une ressource Kubernetes."""
    meta = ressource.get("metadata") or {}
    return (
        ressource.get("kind", "?"),
        meta.get("namespace", ""),
        meta.get("name", "?"),
    )


def _commentaires(texte: str) -> str:
    """Extrait les commentaires du YAML brut.

    Ils portent l'intention — pourquoi telle limite, pourquoi telle
    exception — que la structure seule ne dit pas. C'est précisément ce
    qu'un `kubectl get -o yaml` ne montre jamais.
    """
    lignes = [
        ligne.strip().lstrip("#").strip()
        for ligne in texte.splitlines()
        if ligne.strip().startswith("#")
    ]
    return " ".join(l for l in lignes if len(l) > 3)


def decouper_fichier(chemin: Path, racine: Path) -> list[dict]:
    """Un chunk par ressource Kubernetes du fichier."""
    try:
        brut = chemin.read_text(errors="replace")
    except OSError:
        return []

    relatif = str(chemin.relative_to(racine))
    commentaires = _commentaires(brut)

    try:
        documents = list(yaml.safe_load_all(brut))
    except yaml.YAMLError:
        # Un fichier illisible reste indexable comme texte : mieux vaut
        # une recherche imprécise que pas de recherche du tout.
        return [{
            "texte": f"[Fichier {relatif}]\n{brut[:TAILLE_MAX]}",
            "titre": chemin.name,
            "source": "infra",
            "fichier": relatif,
        }]

    chunks = []
    for doc in documents:
        if not isinstance(doc, dict) or not doc.get("kind"):
            continue

        kind, namespace, nom = _identite(doc)
        if kind in KINDS_IGNORES:
            continue

        emplacement = f"{namespace}/{nom}" if namespace else nom
        entete = f"[{kind} {emplacement} · manifest {relatif}]"

        corps = yaml.safe_dump(
            _nettoyer(doc), default_flow_style=False, allow_unicode=True,
            sort_keys=False,
        )
        if len(corps) > TAILLE_MAX:
            corps = corps[:TAILLE_MAX] + "\n# [... tronqué]"

        texte = entete + "\n"
        if commentaires:
            texte += f"# Intention : {commentaires[:400]}\n"
        texte += corps

        chunks.append({
            "texte": texte,
            "titre": f"{kind} {emplacement}",
            "source": "infra",
            "fichier": relatif,
        })

    return chunks


def collecter(racine: Path) -> list[dict]:
    """Parcourt un dépôt GitOps et produit un chunk par ressource."""
    if not racine.exists():
        return []

    chunks = []
    for chemin in sorted(racine.rglob("*")):
        if not chemin.is_file():
            continue
        if ".git" in chemin.parts:
            continue
        if chemin.suffix not in {".yml", ".yaml"}:
            continue
        chunks.extend(decouper_fichier(chemin, racine))
    return chunks


def collecter_documentation(racine: Path) -> list[dict]:
    """Indexe les runbooks et notes du dépôt, découpés par section.

    Contrairement aux manifests, ces documents sont du markdown : le
    découpage par titre s'applique.
    """
    from devops_agent.ingestion import chunking

    if not racine.exists():
        return []

    chunks = []
    for chemin in sorted(racine.rglob("*.md")):
        if ".git" in chemin.parts:
            continue
        relatif = str(chemin.relative_to(racine))
        for morceau in chunking.decouper(chemin.read_text(errors="replace")):
            chunks.append({
                "texte": morceau["texte"],
                "titre": morceau["titre"],
                "source": "runbook",
                "fichier": relatif,
            })
    return chunks
