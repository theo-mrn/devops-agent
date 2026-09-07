"""Règles de détection d'anomalie, une par type de ressource.

Un pod sain ne garantit pas une application saine : le service devant lui
peut n'avoir aucun endpoint, son volume rester non lié, son rollout être
bloqué. Surveiller les pods seuls laisse ces pannes invisibles.

Chaque détecteur reçoit un objet Kubernetes brut et renvoie soit None
(tout va bien), soit un couple (état, gravité) décrivant l'anomalie.

Aucun appel au modèle ici : ce sont des règles déterministes, testables
et instantanées. L'IA n'intervient qu'ensuite, pour diagnostiquer.
"""

from datetime import datetime, timezone

from devops_agent.agent import overview

# Un objet qui vient d'être créé traverse normalement des états
# transitoires. On lui laisse ce délai avant de le juger.
GRACE_SECONDES = 120


def _age(objet: dict) -> float:
    """Âge de l'objet en secondes, 0 si la date est illisible."""
    date = objet.get("metadata", {}).get("creationTimestamp")
    if not date:
        return 0.0
    try:
        d = datetime.fromisoformat(date.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - d).total_seconds()
    except ValueError:
        return 0.0


# ── Pods ─────────────────────────────────────────────────────────

def pod(objet: dict) -> tuple[str, str] | None:
    """Reprend la logique éprouvée de l'aperçu."""
    from devops_agent.agent.watcher import ETATS_ANORMAUX, _gravite

    etat, _ = overview._etat_pod(objet)
    gravite = _gravite(etat)
    return (etat, gravite) if gravite else None


# ── Services ─────────────────────────────────────────────────────

def endpoints(objet: dict) -> tuple[str, str] | None:
    """Un Service sans endpoint ne route vers rien.

    C'est la panne la plus sournoise : les pods tournent, le Service
    existe, et pourtant l'application est injoignable. La cause habituelle
    est un sélecteur qui ne correspond à aucun pod.
    """
    if _age(objet) < GRACE_SECONDES:
        return None

    nom = objet.get("metadata", {}).get("name", "")
    # Les Endpoints d'un Service « headless » ou géré manuellement
    # peuvent légitimement être vides.
    if nom.startswith("kube-") or nom in {"kubernetes"}:
        return None

    sous_ensembles = objet.get("subsets") or []
    adresses = sum(len(s.get("addresses") or []) for s in sous_ensembles)
    if adresses == 0:
        return ("SansEndpoint", "grave")

    # Des adresses « not ready » signalent des pods qui ne passent pas
    # leur readiness probe.
    non_pretes = sum(len(s.get("notReadyAddresses") or []) for s in sous_ensembles)
    if non_pretes and adresses == 0:
        return ("EndpointsNonPrets", "grave")
    return None


# ── Deployments ──────────────────────────────────────────────────

def deployment(objet: dict) -> tuple[str, str] | None:
    """Un rollout qui n'aboutit pas, ou des replicas manquants."""
    if _age(objet) < GRACE_SECONDES:
        return None

    spec, statut = objet.get("spec", {}), objet.get("status", {})
    voulu = spec.get("replicas", 1)
    pret = statut.get("readyReplicas", 0)

    if voulu == 0:      # arrêt volontaire
        return None

    # Une condition Progressing à False avec ProgressDeadlineExceeded
    # signale un rollout définitivement bloqué.
    for condition in statut.get("conditions", []):
        if (condition.get("type") == "Progressing"
                and condition.get("status") == "False"):
            return ("RolloutBloque", "grave")

    if pret == 0:
        return ("AucunReplicaPret", "critique")
    if pret < voulu:
        return (f"ReplicasIncomplets({pret}/{voulu})", "surveillance")
    return None


# ── StatefulSets ─────────────────────────────────────────────────

def statefulset(objet: dict) -> tuple[str, str] | None:
    if _age(objet) < GRACE_SECONDES:
        return None
    spec, statut = objet.get("spec", {}), objet.get("status", {})
    voulu, pret = spec.get("replicas", 1), statut.get("readyReplicas", 0)
    if voulu == 0:
        return None
    if pret == 0:
        return ("AucunReplicaPret", "critique")
    if pret < voulu:
        return (f"ReplicasIncomplets({pret}/{voulu})", "surveillance")
    return None


# ── PersistentVolumeClaims ───────────────────────────────────────

def pvc(objet: dict) -> tuple[str, str] | None:
    """Un PVC non lié bloque tout pod qui l'attend."""
    if _age(objet) < GRACE_SECONDES:
        return None
    phase = objet.get("status", {}).get("phase")
    if phase == "Pending":
        return ("VolumeNonLie", "grave")
    if phase == "Lost":
        return ("VolumePerdu", "critique")
    return None


# ── Nœuds ────────────────────────────────────────────────────────

def node(objet: dict) -> tuple[str, str] | None:
    """Un nœud NotReady ou sous pression met en danger tout ce qu'il porte."""
    conditions = {
        c["type"]: c["status"]
        for c in objet.get("status", {}).get("conditions", [])
    }

    if conditions.get("Ready") != "True":
        return ("NoeudNotReady", "critique")

    for pression, libelle in (
        ("MemoryPressure", "PressionMemoire"),
        ("DiskPressure", "PressionDisque"),
        ("PIDPressure", "PressionPID"),
    ):
        if conditions.get(pression) == "True":
            return (libelle, "critique")

    # Un nœud marqué non planifiable est souvent une maintenance en
    # cours — à signaler sans alarmer.
    if objet.get("spec", {}).get("unschedulable"):
        return ("NoeudNonPlanifiable", "surveillance")
    return None


# ── Jobs ─────────────────────────────────────────────────────────

def job(objet: dict) -> tuple[str, str] | None:
    """Un Job en échec définitif."""
    statut = objet.get("status", {})
    for condition in statut.get("conditions", []):
        if condition.get("type") == "Failed" and condition.get("status") == "True":
            return ("JobEchoue", "grave")
    if statut.get("failed", 0) > 0 and not statut.get("succeeded"):
        return (f"JobEnEchec({statut['failed']} tentatives)", "surveillance")
    return None


# ── Registre ─────────────────────────────────────────────────────
# Ressource kubectl → (fonction de détection, libellé court)

DETECTEURS = {
    "pods": (pod, "pod"),
    "endpoints": (endpoints, "service"),
    "deployments": (deployment, "deployment"),
    "statefulsets": (statefulset, "statefulset"),
    "pvc": (pvc, "pvc"),
    "nodes": (node, "node"),
    "jobs": (job, "job"),
}

# Ressources surveillées par défaut. Chacune ouvre une connexion watch ;
# limiter la liste réduit la charge sur l'API server du cluster.
PAR_DEFAUT = ["pods", "endpoints", "deployments", "pvc", "nodes"]
