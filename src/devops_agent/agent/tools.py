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

import devops_agent.core.config
from devops_agent.agent import sanitizer

# Verbes kubectl autorisés. Tout le reste est refusé.
VERBES_AUTORISES = {
    "get", "describe", "logs", "top", "events", "version", "api-resources",
    "explain", "cluster-info",
}

# Ressources dont le contenu ne doit jamais sortir, même en lecture.
RESSOURCES_INTERDITES = {"secret", "secrets"}

# Chemin du compte de service monté par Kubernetes dans tout pod.
# Sa présence signale que l'agent tourne DANS le cluster : kubectl s'y
# authentifie alors seul, sans kubeconfig.
JETON_IN_CLUSTER = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")


def dans_le_cluster() -> bool:
    """L'agent tourne-t-il à l'intérieur du cluster qu'il surveille ?"""
    return JETON_IN_CLUSTER.exists()


def contexte_kubernetes() -> str:
    """Décrit comment l'agent accède au cluster, pour l'affichage."""
    if dans_le_cluster():
        namespace = Path(
            "/var/run/secrets/kubernetes.io/serviceaccount/namespace"
        )
        ns = namespace.read_text().strip() if namespace.exists() else "?"
        return f"in-cluster (ServiceAccount, namespace {ns})"

    kubeconfig = os.environ.get("KUBECONFIG", "~/.kube/config")
    return f"externe (kubeconfig {kubeconfig})"


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


def verifier_commande(commande: str) -> str | None:
    """Renvoie le motif de refus, ou None si la commande est autorisée.

    Séparé de `kubectl()` pour que les garde-fous soient testables sans
    cluster : la vérification est pure, l'exécution ne l'est pas.
    """
    try:
        _verifier_kubectl(commande.split())
        return None
    except OutilRefuse as e:
        return str(e)


def kubectl(commande: str) -> str:
    """Exécute une commande kubectl en lecture seule.

    `commande` est la commande SANS le préfixe « kubectl », par exemple :
        get pods -n prod
        describe pod payment-svc-abc -n prod
        logs payment-svc-abc -n prod --previous --tail=50
    """
    if not shutil.which("kubectl"):
        return "[erreur] kubectl n'est pas installé ou absent du PATH"

    motif = verifier_commande(commande)
    if motif:
        return f"[refusé] {motif}"

    args = commande.split()
    try:
        r = subprocess.run(
            ["kubectl", *args],
            capture_output=True, text=True, timeout=TIMEOUT_KUBECTL,
        )
    except subprocess.TimeoutExpired:
        return f"[erreur] kubectl n'a pas répondu en {TIMEOUT_KUBECTL}s"

    # La troncature et le masquage sont appliqués par `executer()`.
    return r.stdout or r.stderr or "(aucune sortie)"


def rag_disponible() -> bool:
    """La recherche documentaire est-elle utilisable ?

    Elle demande ~2 Go de dépendances (PyTorch, sentence-transformers) et
    un index construit. Un agent qui ne fait que du diagnostic cluster
    s'en passe : quatre de ses cinq outils n'en ont pas besoin.
    """
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        return False

    from devops_agent.core import config
    return config.INDEX.exists()


def chercher_documentation(question: str) -> str:
    """Recherche dans la documentation indexée (le pipeline RAG)."""
    if not rag_disponible():
        # Dégrader proprement plutôt que planter : le modèle reçoit une
        # explication utilisable et poursuit avec ses autres outils.
        return (
            "[indisponible] La recherche documentaire demande les dépendances "
            "optionnelles et un index construit :\n"
            "    uv sync --extra rag\n"
            "    devops-agent index\n"
            "Poursuis le diagnostic avec les autres outils."
        )

    from devops_agent.retrieval import pipeline

    resultats = pipeline.chercher_rerank(question, k=3)
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

    return cible.read_text(errors="replace")


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


def ressources_pod(pod: str, namespace: str = "default") -> str:
    """Limites configurées et consommation réelle d'un pod, côte à côte.

    Un correctif de dimensionnement mémoire n'a de valeur que s'il part
    des vraies valeurs. Cet outil évite à l'agent de croiser lui-même
    trois commandes — et lui évite surtout de proposer « 1Gi » au jugé.
    """
    lignes = []

    limites = kubectl(
        f"get pod {pod} -n {namespace} "
        "-o jsonpath={.spec.containers[*].resources}"
    )
    lignes.append(f"Configuré : {limites.strip() or '(aucune limite définie)'}")

    consommation = kubectl(f"top pod {pod} -n {namespace} --no-headers")
    if consommation.startswith(("[erreur]", "[refusé]")):
        lignes.append("Consommation : indisponible (metrics-server absent ?)")
    else:
        lignes.append(f"Consommation actuelle : {consommation.strip()}")

    etat = kubectl(
        f"get pod {pod} -n {namespace} "
        "-o jsonpath={.status.containerStatuses[*].lastState}"
    )
    if etat.strip() and etat.strip() != "{}":
        lignes.append(f"État précédent : {etat.strip()}")

    return "\n".join(lignes)


def rafraichir_apercu() -> str:
    """Reprend un instantané du cluster, en ignorant le cache.

    À appeler quand l'agent soupçonne que l'état a changé depuis l'aperçu
    initial — par exemple après avoir constaté un redémarrage.
    """
    from devops_agent.agent import overview
    return overview.rafraichir()


def auditer_permissions() -> list[tuple[str, bool, bool]]:
    """Vérifie ce que l'agent peut RÉELLEMENT faire sur le cluster.

    Le code refuse les écritures, mais c'est le serveur d'API qui doit
    les rendre impossibles. Cette fonction interroge `kubectl auth can-i`
    pour confirmer que le RBAC est bien en place.

    Renvoie (action, autorisé, devrait_être_autorisé).
    """
    lectures = [
        "get pods", "list pods", "get pods/log", "watch pods",
        "get services", "get endpoints", "get deployments",
        "get persistentvolumeclaims", "get nodes", "get events",
    ]
    ecritures = [
        "delete pods", "create pods", "patch deployments",
        "update deployments", "delete nodes", "create secrets",
        "get secrets", "create pods/exec",
    ]

    resultats = []
    for action, attendu in [(a, True) for a in lectures] + [(a, False) for a in ecritures]:
        verbe, _, ressource = action.partition(" ")
        try:
            r = subprocess.run(
                ["kubectl", "auth", "can-i", verbe, ressource,
                 "--all-namespaces"],
                capture_output=True, text=True, timeout=10,
            )
            autorise = r.stdout.strip() == "yes"
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue
        resultats.append((action, autorise, attendu))
    return resultats


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
        "name": "ressources_pod",
        "description": (
            "Donne côte à côte les limites configurées d'un pod, sa consommation "
            "actuelle et son état de terminaison précédent. À utiliser avant de "
            "proposer un redimensionnement mémoire ou CPU : le correctif doit "
            "partir des valeurs réelles, pas d'une estimation."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pod": {"type": "string", "description": "Nom exact du pod"},
                "namespace": {"type": "string", "description": "Namespace du pod"},
            },
            "required": ["pod", "namespace"],
            "additionalProperties": False,
        },
    },
    {
        "name": "rafraichir_apercu",
        "description": (
            "Reprend un instantané de l'état du cluster (nœuds, pods en anomalie, "
            "pression sur les ressources). L'aperçu initial est déjà fourni dans la "
            "question : n'appeler cet outil que si tu soupçonnes un changement depuis, "
            "ou si l'aperçu date de plus d'une minute."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
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
    "rafraichir_apercu": rafraichir_apercu,
    "ressources_pod": ressources_pod,
}


def executer(nom: str, arguments: dict) -> str:
    """Exécute l'outil demandé par le modèle, puis assainit sa sortie.

    L'assainissement est fait ICI et nulle part ailleurs : un point de
    passage unique garantit qu'aucune sortie d'outil ne part vers l'API
    sans être filtrée. Le faire dans chaque fonction exposerait au
    premier oubli.
    """
    fonction = IMPLEMENTATIONS.get(nom)
    if fonction is None:
        return f"[erreur] outil « {nom} » inconnu"
    try:
        sortie = fonction(**arguments)
    except TypeError as e:
        return f"[erreur] arguments invalides pour « {nom} » : {e}"
    except Exception as e:
        return f"[erreur] {type(e).__name__} : {e}"

    return sanitizer.assainir(sortie)
