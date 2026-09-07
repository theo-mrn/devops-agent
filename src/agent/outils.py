"""Outils de l'agent — LECTURE SEULE STRICTE.

Principe de sûreté : l'agent ne peut RIEN modifier. Chaque outil est une
fonction Python explicite, jamais un shell générique. Le modèle demande,
ce code exécute — et ce code refuse tout ce qui n'est pas une lecture.

Trois niveaux de protection :
  1. aucune fonction d'écriture n'existe (rien à contourner) ;
  2. les verbes kubectl sont sur liste blanche (`get`, `describe`, `logs`,
     `top`, `events`, `version`, `api-resources`) ;
  3. les namespaces peuvent être restreints par configuration.

Les Secrets ne sont jamais retournés en clair, même en lecture : `kubectl
get secret` expose des identifiants encodés en base64, trivialement
décodables.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

# Verbes kubectl autorisés. Tout le reste est refusé.
VERBES_AUTORISES = {
    "get", "describe", "logs", "top", "events", "version", "api-resources",
    "explain", "cluster-info",
}

# Ressources dont le contenu ne doit jamais sortir, même en lecture.
RESSOURCES_INTERDITES = {"secret", "secrets"}

# Restriction optionnelle des namespaces : RAG_NAMESPACES="prod,staging".
# Vide = tous autorisés.
NAMESPACES_AUTORISES = {
    n.strip() for n in os.environ.get("RAG_NAMESPACES", "").split(",") if n.strip()
}

TIMEOUT_KUBECTL = int(os.environ.get("RAG_KUBECTL_TIMEOUT", "30"))
LIMITE_SORTIE = 8000  # caractères ; au-delà, la sortie est tronquée


class OutilRefuse(Exception):
    """L'appel viole une règle de sûreté."""


def _verifier_kubectl(args: list[str]) -> None:
    """Refuse tout ce qui n'est pas une lecture autorisée."""
    if not args:
        raise OutilRefuse("aucune commande fournie")

    verbe = args[0]
    if verbe not in VERBES_AUTORISES:
        raise OutilRefuse(
            f"verbe « {verbe} » interdit. Autorisés : {', '.join(sorted(VERBES_AUTORISES))}"
        )

    joint = " ".join(args).lower()

    # Les Secrets fuiteraient des identifiants même via `get -o yaml`.
    for interdite in RESSOURCES_INTERDITES:
        if re.search(rf"\b{interdite}\b", joint):
            raise OutilRefuse(
                "l'accès aux Secrets est interdit : leur contenu est encodé "
                "en base64, donc lisible en clair"
            )

    # Un flag d'écriture glissé dans les arguments.
    for suspect in ("--force", "-f ", "--filename", "--patch", "--overwrite"):
        if suspect in joint:
            raise OutilRefuse(f"argument « {suspect.strip()} » interdit en lecture seule")

    # Restriction de namespace, si configurée.
    if NAMESPACES_AUTORISES:
        m = re.search(r"(?:-n|--namespace)[= ]+(\S+)", joint)
        if m and m.group(1) not in NAMESPACES_AUTORISES:
            raise OutilRefuse(
                f"namespace « {m.group(1)} » non autorisé. "
                f"Autorisés : {', '.join(sorted(NAMESPACES_AUTORISES))}"
            )
        if not m and "--all-namespaces" in joint:
            raise OutilRefuse("--all-namespaces interdit quand une liste est configurée")


def kubectl(commande: str) -> str:
    """Exécute une commande kubectl en lecture seule.

    `commande` est la commande SANS le préfixe « kubectl », par exemple :
        get pods -n prod
        describe pod payment-svc-abc -n prod
        logs payment-svc-abc -n prod --previous --tail=50
    """
    if not shutil.which("kubectl"):
        return "[erreur] kubectl n'est pas installé ou absent du PATH"

    args = commande.split()
    try:
        _verifier_kubectl(args)
    except OutilRefuse as e:
        return f"[refusé] {e}"

    try:
        r = subprocess.run(
            ["kubectl", *args],
            capture_output=True, text=True, timeout=TIMEOUT_KUBECTL,
        )
    except subprocess.TimeoutExpired:
        return f"[erreur] kubectl n'a pas répondu en {TIMEOUT_KUBECTL}s"

    sortie = r.stdout or r.stderr or "(aucune sortie)"
    if len(sortie) > LIMITE_SORTIE:
        sortie = sortie[:LIMITE_SORTIE] + f"\n[... tronqué, {len(sortie)} caractères au total]"
    return sortie


def chercher_documentation(question: str) -> str:
    """Recherche dans la documentation indexée (le pipeline RAG)."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import rag2

    resultats = rag2.chercher_rerank(question, k=3)
    if not resultats:
        return "Aucun résultat dans la documentation indexée."

    morceaux = []
    for chunk, score in resultats:
        morceaux.append(
            f"[{chunk['source']}/{chunk['fichier']} — {chunk['titre']}] (pertinence {score:.2f})\n"
            f"{chunk['texte'][:1500]}"
        )
    return "\n\n---\n\n".join(morceaux)


def lire_fichier(chemin: str) -> str:
    """Lit un fichier du dépôt d'infrastructure.

    Restreint à RAG_INFRA_DIR (défaut : le dossier du projet) pour empêcher
    la lecture de ~/.ssh, /etc/shadow ou de tout autre fichier sensible.
    """
    racine = Path(os.environ.get("RAG_INFRA_DIR", Path.cwd())).resolve()
    try:
        cible = (racine / chemin).resolve()
    except (OSError, ValueError) as e:
        return f"[erreur] chemin invalide : {e}"

    # Empêche la traversée par « ../ » ou par lien symbolique.
    if not cible.is_relative_to(racine):
        return f"[refusé] le chemin sort de {racine}"
    if not cible.is_file():
        return f"[erreur] fichier introuvable : {chemin}"
    if cible.stat().st_size > 200_000:
        return f"[erreur] fichier trop volumineux ({cible.stat().st_size} octets)"

    contenu = cible.read_text(errors="replace")
    if len(contenu) > LIMITE_SORTIE:
        contenu = contenu[:LIMITE_SORTIE] + "\n[... tronqué]"
    return contenu


def lister_fichiers(motif: str = "**/*.yaml") -> str:
    """Liste les fichiers d'infrastructure correspondant à un motif glob."""
    racine = Path(os.environ.get("RAG_INFRA_DIR", Path.cwd())).resolve()
    try:
        trouves = sorted(
            str(p.relative_to(racine))
            for p in racine.glob(motif)
            if p.is_file() and ".git" not in p.parts
        )[:100]
    except (OSError, ValueError) as e:
        return f"[erreur] motif invalide : {e}"

    return "\n".join(trouves) if trouves else f"aucun fichier pour « {motif} »"


# ── Déclarations pour le modèle ──────────────────────────────────
# Les descriptions comptent autant que le code : c'est sur elles que le
# modèle décide quel outil appeler.

DEFINITIONS = [
    {
        "name": "chercher_documentation",
        "description": (
            "Cherche dans la documentation technique indexée (Kubernetes, Terraform, "
            "Docker, Linux, CI/CD, plus les runbooks internes). À utiliser pour toute "
            "question de procédure, de syntaxe ou de concept. Recherche sémantique : "
            "formuler une question complète, pas des mots-clés."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "La question, formulée en langage naturel",
                }
            },
            "required": ["question"],
            "additionalProperties": False,
        },
    },
    {
        "name": "kubectl",
        "description": (
            "Exécute une commande kubectl EN LECTURE SEULE sur le cluster. "
            "Verbes autorisés : get, describe, logs, top, events, version, "
            "api-resources, explain, cluster-info. Toute écriture est refusée. "
            "L'accès aux Secrets est interdit. "
            "Passer la commande sans le préfixe « kubectl », par exemple "
            "« get pods -n prod » ou « logs mon-pod -n prod --previous --tail=50 »."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "commande": {
                    "type": "string",
                    "description": "La commande sans « kubectl », ex. « describe pod X -n prod »",
                }
            },
            "required": ["commande"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lire_fichier",
        "description": (
            "Lit un fichier du dépôt d'infrastructure (manifest Kubernetes, "
            "configuration Terraform, runbook). Chemin relatif à la racine du dépôt."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "chemin": {
                    "type": "string",
                    "description": "Chemin relatif, ex. « manifests/prod/payment.yaml »",
                }
            },
            "required": ["chemin"],
            "additionalProperties": False,
        },
    },
    {
        "name": "lister_fichiers",
        "description": (
            "Liste les fichiers du dépôt d'infrastructure selon un motif glob. "
            "Utile pour découvrir la structure avant de lire un fichier précis."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "motif": {
                    "type": "string",
                    "description": "Motif glob, ex. « **/*.tf » ou « manifests/**/*.yaml »",
                }
            },
            "required": ["motif"],
            "additionalProperties": False,
        },
    },
]

# Table de dispatch : nom déclaré au modèle → fonction Python.
IMPLEMENTATIONS = {
    "chercher_documentation": chercher_documentation,
    "kubectl": kubectl,
    "lire_fichier": lire_fichier,
    "lister_fichiers": lister_fichiers,
}


def executer(nom: str, arguments: dict) -> str:
    """Exécute l'outil demandé par le modèle."""
    fonction = IMPLEMENTATIONS.get(nom)
    if fonction is None:
        return f"[erreur] outil « {nom} » inconnu"
    try:
        return fonction(**arguments)
    except TypeError as e:
        return f"[erreur] arguments invalides pour « {nom} » : {e}"
    except Exception as e:
        return f"[erreur] {type(e).__name__} : {e}"
