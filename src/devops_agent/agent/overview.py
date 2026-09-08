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

from devops_agent.agent import sanitizer, tools

# Durée de vie de l'instantané. Un cluster bouge, mais pas à la seconde.
TTL = int(os.environ.get("RAG_APERCU_TTL", "60"))

# États de pod considérés comme sains. Tout le reste est signalé.
ETATS_SAINS = {"Running", "Succeeded", "Completed"}

# Au-delà, on ne liste plus les anomalies une par une : on agrège.
MAX_ANOMALIES = 15

# Redémarrages par jour au-delà desquels un pod est jugé instable.
# En dessous, c'est du bruit de fond : un pod peut cumuler des centaines
# de redémarrages sur plusieurs mois sans que ce soit un incident.
SEUIL_PAR_JOUR = float(os.environ.get("RAG_SEUIL_PAR_JOUR", "2.0"))

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


def _age_heures(pod: dict) -> float:
    """Âge du pod en heures, 0 si la date est illisible."""
    from datetime import datetime, timezone
    debut = pod.get("status", {}).get("startTime")
    if not debut:
        return 0.0
    try:
        d = datetime.fromisoformat(debut.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - d).total_seconds() / 3600
    except ValueError:
        return 0.0


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
        age = _age_heures(p)

        # 124 redémarrages en 108 jours est un bruit de fond ; 124 en une
        # heure est une urgence. Seule la fréquence distingue les deux.
        par_jour = redemarrages / max(age / 24, 0.04) if redemarrages else 0.0

        resultat.append({
            "nom": p["metadata"]["name"],
            "namespace": p["metadata"]["namespace"],
            "etat": etat,
            "redemarrages": redemarrages,
            "par_jour": par_jour,
            "age_h": age,
            "sain": etat in ETATS_SAINS and par_jour < SEUIL_PAR_JOUR,
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
        for p in sorted(malades, key=lambda x: -x.get("par_jour", 0))[:MAX_ANOMALIES]:
            suffixe = ""
            if p["redemarrages"]:
                suffixe = (
                    f"  ({p['redemarrages']} redémarrages, "
                    f"{p.get('par_jour', 0):.1f}/jour)"
                )
            lignes.append(f"  {p['namespace']}/{p['nom']}  {p['etat']}{suffixe}")

        if len(malades) > MAX_ANOMALIES:
            # Au-delà du seuil, agréger plutôt que d'inonder le contexte.
            restants = Counter(p["etat"] for p in malades[MAX_ANOMALIES:])
            detail = ", ".join(f"{n}× {e}" for e, n in restants.most_common())
            lignes.append(f"  [... et {len(malades) - MAX_ANOMALIES} autres : {detail}]")

    # Les pods qui cumulent des redémarrages depuis longtemps sans être
    # instables aujourd'hui : à surveiller, pas à diagnostiquer.
    chroniques = [
        p for p in pods
        if p.get("sain") and p.get("redemarrages", 0) >= 20
    ]
    if chroniques:
        noms = ", ".join(
            f"{p['namespace']}/{p['nom']} ({p['redemarrages']})"
            for p in sorted(chroniques, key=lambda x: -x["redemarrages"])[:5]
        )
        lignes.append(f"\nRedémarrages cumulés (stables actuellement) : {noms}")

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
    # L'aperçu contourne `tools.executer()` : il doit être assaini
    # explicitement. Un nom de ressource peut porter un identifiant.
    texte = sanitizer.masquer(_formater(noeuds, pods, version))

    _cache["texte"] = texte
    _cache["date"] = maintenant
    return texte


def rafraichir() -> str:
    """Force un nouvel instantané, en ignorant le cache."""
    return apercu(forcer=True)
