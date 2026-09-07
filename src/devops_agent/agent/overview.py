"""Aperçu de cluster : un résumé condensé, pas un dump.

Un `kubectl get pods -A -o wide` sur un cluster de production fait
facilement 80 000 tokens. Le donner au modèle à chaque question sature la
fenêtre de contexte et coûte cher pour rien.

Ce module collecte l'état complet, puis le condense en ~2 000 tokens en
gardant ce qui est actionnable :

    - la topologie (nœuds, versions, namespaces et leur charge) ;
    - les ANOMALIES en tête (pods non sains, redémarrages, évictions) ;
    - la pression sur les ressources (nœuds saturés).

L'agent voit immédiatement ce qui ne va pas, puis creuse avec `describe`
sur ce qui l'intéresse. Le résumé oriente, les outils approfondissent.

Un cache à durée de vie évite de reconstruire l'aperçu à chaque question :
deux questions posées coup sur coup partagent le même instantané.
"""

import json
import os
import shutil
import subprocess
import time
from collections import Counter

from devops_agent.agent import tools

# Durée de vie de l'instantané. Un cluster bouge, mais pas à la seconde.
TTL = int(os.environ.get("RAG_APERCU_TTL", "60"))

# États de pod considérés comme sains. Tout le reste est signalé.
ETATS_SAINS = {"Running", "Succeeded", "Completed"}

# Au-delà, on ne liste plus les anomalies une par une : on agrège.
MAX_ANOMALIES = 15

_cache: dict = {}


def _kubectl_json(commande: str) -> dict | None:
    """Exécute une commande kubectl et parse sa sortie JSON.

    Passe par les mêmes garde-fous que l'agent : impossible de contourner
    la lecture seule depuis ce module.
    """
    if tools.verifier_commande(commande) is not None:
        return None
    if not shutil.which("kubectl"):
        return None

    try:
        r = subprocess.run(
            ["kubectl", *commande.split(), "-o", "json"],
            capture_output=True, text=True, timeout=tools.TIMEOUT_KUBECTL,
        )
        if r.returncode != 0:
            return None
        return json.loads(r.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


def _etat_pod(pod: dict) -> tuple[str, int]:
    """État lisible d'un pod et son nombre de redémarrages.

    La phase seule ne suffit pas : un pod en CrashLoopBackOff est en phase
    `Running`. La vraie information est dans les états de conteneurs.
    """
    statut = pod.get("status", {})
    phase = statut.get("phase", "Unknown")
    redemarrages = 0
    raison = None

    for conteneur in statut.get("containerStatuses", []) or []:
        redemarrages += conteneur.get("restartCount", 0)
        attente = conteneur.get("state", {}).get("waiting")
        if attente and attente.get("reason"):
            raison = attente["reason"]          # CrashLoopBackOff, ImagePullBackOff…
        termine = conteneur.get("lastState", {}).get("terminated")
        if termine and termine.get("reason") == "OOMKilled":
            raison = raison or "OOMKilled (précédent)"

    return raison or phase, redemarrages


def _collecter_noeuds() -> list[dict]:
    donnees = _kubectl_json("get nodes")
    if not donnees:
        return []

    noeuds = []
    for n in donnees.get("items", []):
        conditions = {
            c["type"]: c["status"] for c in n.get("status", {}).get("conditions", [])
        }
        # Une condition de pression est vraie quand le nœud manque de
        # quelque chose : c'est le signal à remonter.
        pressions = [
            t.replace("Pressure", "")
            for t in ("MemoryPressure", "DiskPressure", "PIDPressure")
            if conditions.get(t) == "True"
        ]
        noeuds.append({
            "nom": n["metadata"]["name"],
            "pret": conditions.get("Ready") == "True",
            "version": n.get("status", {}).get("nodeInfo", {}).get("kubeletVersion", "?"),
            "pressions": pressions,
        })
    return noeuds


def _collecter_pods() -> list[dict]:
    commande = "get pods"
    if tools.NAMESPACES_AUTORISES:
        # Sans restriction on balaie tout ; avec, on interroge namespace
        # par namespace pour ne rien lire d'interdit.
        pods = []
        for ns in sorted(tools.NAMESPACES_AUTORISES):
            donnees = _kubectl_json(f"get pods -n {ns}")
            if donnees:
                pods.extend(donnees.get("items", []))
    else:
        donnees = _kubectl_json("get pods --all-namespaces")
        pods = donnees.get("items", []) if donnees else []

    resultat = []
    for p in pods:
        etat, redemarrages = _etat_pod(p)
        resultat.append({
            "nom": p["metadata"]["name"],
            "namespace": p["metadata"]["namespace"],
            "etat": etat,
            "redemarrages": redemarrages,
            "sain": etat in ETATS_SAINS and redemarrages < 5,
        })
    return resultat


def _formater(noeuds: list[dict], pods: list[dict], version: str) -> str:
    lignes = []

    # ── Topologie ────────────────────────────────────────────────
    prets = sum(1 for n in noeuds if n["pret"])
    lignes.append(
        f"Cluster : {len(noeuds)} nœud(s), {prets} prêt(s) · Kubernetes {version}"
    )

    non_prets = [n["nom"] for n in noeuds if not n["pret"]]
    if non_prets:
        lignes.append(f"⚠ Nœuds NotReady : {', '.join(non_prets)}")

    sous_pression = [
        f"{n['nom']} ({'/'.join(n['pressions'])})" for n in noeuds if n["pressions"]
    ]
    if sous_pression:
        lignes.append(f"⚠ Nœuds sous pression : {', '.join(sous_pression)}")

    # ── Namespaces ───────────────────────────────────────────────
    par_ns = Counter(p["namespace"] for p in pods)
    if par_ns:
        resume_ns = ", ".join(
            f"{ns} ({n})" for ns, n in sorted(par_ns.items(), key=lambda x: -x[1])[:10]
        )
        lignes.append(f"\nNamespaces : {resume_ns}")

    # ── Anomalies ────────────────────────────────────────────────
    # C'est la partie utile : ce qui ne tourne pas rond, en tête.
    malades = [p for p in pods if not p["sain"]]
    if not malades:
        lignes.append(f"\n✓ Les {len(pods)} pods sont sains.")
    else:
        lignes.append(f"\n⚠ {len(malades)} pod(s) en anomalie sur {len(pods)} :")
        for p in sorted(malades, key=lambda x: -x["redemarrages"])[:MAX_ANOMALIES]:
            suffixe = f"  ({p['redemarrages']} redémarrages)" if p["redemarrages"] else ""
            lignes.append(f"  {p['namespace']}/{p['nom']}  {p['etat']}{suffixe}")

        if len(malades) > MAX_ANOMALIES:
            # Au-delà du seuil, agréger plutôt que d'inonder le contexte.
            restants = Counter(p["etat"] for p in malades[MAX_ANOMALIES:])
            detail = ", ".join(f"{n}× {e}" for e, n in restants.most_common())
            lignes.append(f"  [... et {len(malades) - MAX_ANOMALIES} autres : {detail}]")

    return "\n".join(lignes)


def apercu(forcer: bool = False) -> str:
    """Aperçu condensé du cluster, mis en cache pendant TTL secondes."""
    maintenant = time.time()
    if not forcer and _cache.get("texte") and maintenant - _cache["date"] < TTL:
        age = int(maintenant - _cache["date"])
        return f"{_cache['texte']}\n\n[instantané pris il y a {age}s]"

    if not shutil.which("kubectl"):
        return "[kubectl absent — aucun accès au cluster]"

    donnees_version = _kubectl_json("version")
    if donnees_version is None:
        return (
            "[cluster injoignable — vérifier le kubeconfig et la connectivité]"
        )
    version = (
        donnees_version.get("serverVersion", {}).get("gitVersion", "?")
        if isinstance(donnees_version, dict) else "?"
    )

    noeuds = _collecter_noeuds()
    pods = _collecter_pods()
    texte = _formater(noeuds, pods, version)

    _cache["texte"] = texte
    _cache["date"] = maintenant
    return texte


def rafraichir() -> str:
    """Force un nouvel instantané, en ignorant le cache."""
    return apercu(forcer=True)
