"""Détection d'anomalies de ressources, à partir de metrics-server.

Les détecteurs d'objets voient qu'un pod a ÉTÉ tué. Les métriques
permettent de voir qu'il VA l'être : un pod à 90 % de sa limite mémoire
finira OOMKilled, et l'alerte arrive avant l'incident plutôt qu'après.

Ce que metrics-server permet :

    consommation instantanée CPU et mémoire, par pod et par nœud

Ce qu'il ne permet pas — il faudrait Prometheus :

    tendances, latence, taux d'erreur, saturation des volumes

⚠️ Une métrique instantanée est bruyante : un pod peut monter à 90 %
pendant dix secondes lors d'un pic normal. On exige donc plusieurs
relevés consécutifs au-dessus du seuil avant de signaler quoi que ce
soit — sans cela, chaque pic coûterait un diagnostic.

Aucun appel au modèle ici.
"""

import json
import os
import re
import shutil
import subprocess
import time
from collections import defaultdict, deque

from devops_agent.agent import tools

# Part de la limite mémoire au-delà de laquelle un pod est en danger.
# 85 % laisse le temps d'agir avant l'OOMKill.
SEUIL_MEMOIRE = float(os.environ.get("RAG_SEUIL_MEMOIRE", "0.85"))

# Part de la capacité d'un nœud au-delà de laquelle il est saturé.
SEUIL_NOEUD = float(os.environ.get("RAG_SEUIL_NOEUD", "0.90"))

# Nombre de relevés consécutifs au-dessus du seuil avant de signaler.
# C'est ce qui distingue un pic transitoire d'une vraie dérive.
RELEVES_CONFIRMATION = int(os.environ.get("RAG_RELEVES_CONFIRMATION", "3"))

# Intervalle entre deux relevés, en secondes.
INTERVALLE = int(os.environ.get("RAG_INTERVALLE_METRIQUES", "60"))


def _quantite_vers_octets(valeur: str) -> float:
    """Convertit une quantité Kubernetes en octets.

    metrics-server renvoie « 69136Ki », les manifests « 2Gi » : les deux
    doivent être comparables.
    """
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([A-Za-z]*)", valeur.strip())
    if not m:
        return 0.0
    nombre, unite = float(m.group(1)), m.group(2)
    facteurs = {
        "": 1,
        "Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4,
        "K": 1000, "k": 1000, "M": 1000**2, "G": 1000**3, "T": 1000**4,
    }
    return nombre * facteurs.get(unite, 1)


def _quantite_vers_millicores(valeur: str) -> float:
    """Convertit une quantité CPU en millicores.

    metrics-server renvoie des nanocores (« 12700236n »), les manifests
    des millicores (« 500m ») ou des cœurs entiers (« 2 »).
    """
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([a-zA-Z]*)", valeur.strip())
    if not m:
        return 0.0
    nombre, unite = float(m.group(1)), m.group(2)
    if unite == "n":
        return nombre / 1_000_000
    if unite == "u":
        return nombre / 1_000
    if unite == "m":
        return nombre
    return nombre * 1000      # cœurs entiers


def _lire(chemin: str) -> dict | None:
    """Lit une ressource de l'API Kubernetes, à travers les garde-fous."""
    if not shutil.which("kubectl"):
        return None
    try:
        r = subprocess.run(
            ["kubectl", "get", "--raw", chemin],
            capture_output=True, text=True, timeout=tools.TIMEOUT_KUBECTL,
        )
        return json.loads(r.stdout) if r.returncode == 0 else None
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        return None


def consommation_pods() -> dict[str, dict]:
    """Consommation actuelle de chaque pod, par identité `ns/nom`."""
    donnees = _lire("/apis/metrics.k8s.io/v1beta1/pods")
    if not donnees:
        return {}

    resultat = {}
    for item in donnees.get("items", []):
        meta = item.get("metadata", {})
        ns, nom = meta.get("namespace"), meta.get("name")
        if not ns or not nom:
            continue
        if ns in tools.NAMESPACES_AUTORISES or not tools.NAMESPACES_AUTORISES:
            memoire = sum(
                _quantite_vers_octets(c["usage"]["memory"])
                for c in item.get("containers", [])
            )
            cpu = sum(
                _quantite_vers_millicores(c["usage"]["cpu"])
                for c in item.get("containers", [])
            )
            resultat[f"{ns}/{nom}"] = {"memoire": memoire, "cpu": cpu}
    return resultat


def limites_pods() -> dict[str, dict]:
    """Limites déclarées de chaque pod. Sans limite, pas de seuil calculable."""
    commande = "get pods --all-namespaces"
    if tools.verifier_commande(commande) is not None:
        return {}
    try:
        r = subprocess.run(
            ["kubectl", *commande.split(), "-o", "json"],
            capture_output=True, text=True, timeout=tools.TIMEOUT_KUBECTL,
        )
        donnees = json.loads(r.stdout) if r.returncode == 0 else {}
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        return {}

    resultat = {}
    for pod in donnees.get("items", []):
        meta = pod["metadata"]
        memoire = cpu = 0.0
        for conteneur in pod.get("spec", {}).get("containers", []):
            limites = conteneur.get("resources", {}).get("limits", {})
            if "memory" in limites:
                memoire += _quantite_vers_octets(limites["memory"])
            if "cpu" in limites:
                cpu += _quantite_vers_millicores(limites["cpu"])
        resultat[f"{meta['namespace']}/{meta['name']}"] = {
            "memoire": memoire, "cpu": cpu
        }
    return resultat


class SurveillanceRessources:
    """Suit la consommation dans le temps et signale les dérives.

    Un relevé isolé ne suffit pas : on exige plusieurs mesures
    consécutives au-dessus du seuil. Un pic de dix secondes ne doit pas
    coûter un diagnostic.
    """

    def __init__(self, confirmations: int = RELEVES_CONFIRMATION):
        self.confirmations = confirmations
        # Historique borné : seuls les derniers relevés comptent.
        self.historique: dict[str, deque] = defaultdict(
            lambda: deque(maxlen=confirmations)
        )
        self.dernier_releve = 0.0
        self.signales: dict[str, float] = {}

    def doit_relever(self) -> bool:
        return time.time() - self.dernier_releve >= INTERVALLE

    def relever(self) -> list[tuple[str, str, str, str]]:
        """Prend un relevé et renvoie les dérives confirmées.

        Chaque entrée : (namespace, pod, état, détail).
        """
        self.dernier_releve = time.time()

        consommation = consommation_pods()
        if not consommation:
            return []
        limites = limites_pods()

        anomalies = []
        for identite, usage in consommation.items():
            limite = limites.get(identite, {})
            memoire_max = limite.get("memoire", 0)

            # Sans limite déclarée, aucun ratio n'est calculable — le pod
            # est en revanche exposé à l'éviction du nœud.
            if memoire_max <= 0:
                self.historique[identite].clear()
                continue

            part = usage["memoire"] / memoire_max
            depasse = part >= SEUIL_MEMOIRE
            self.historique[identite].append(depasse)

            releves = self.historique[identite]
            if len(releves) < self.confirmations or not all(releves):
                continue

            # Ne pas re-signaler le même pod avant une heure.
            if time.time() - self.signales.get(identite, 0) < 3600:
                continue
            self.signales[identite] = time.time()

            ns, _, nom = identite.partition("/")
            anomalies.append((
                ns, nom, "MemoireProcheDeLaLimite",
                f"{usage['memoire'] / 1024**2:.0f}Mi utilisés sur "
                f"{memoire_max / 1024**2:.0f}Mi ({part * 100:.0f} %) "
                f"sur {self.confirmations} relevés consécutifs",
            ))

        return anomalies


def etat_actuel() -> str:
    """Résumé lisible de la pression mémoire, pour l'aperçu et le diagnostic."""
    consommation = consommation_pods()
    if not consommation:
        return "métriques indisponibles (metrics-server absent ?)"

    limites = limites_pods()
    tendus = []
    sans_limite = 0

    for identite, usage in consommation.items():
        memoire_max = limites.get(identite, {}).get("memoire", 0)
        if memoire_max <= 0:
            sans_limite += 1
            continue
        part = usage["memoire"] / memoire_max
        if part >= 0.70:
            tendus.append((part, identite, usage["memoire"], memoire_max))

    if not tendus and not sans_limite:
        return f"{len(consommation)} pods, aucune pression mémoire"

    lignes = []
    for part, identite, utilise, maximum in sorted(tendus, reverse=True)[:8]:
        lignes.append(
            f"  {identite}  {utilise / 1024**2:.0f}Mi / "
            f"{maximum / 1024**2:.0f}Mi  ({part * 100:.0f} %)"
        )
    if sans_limite:
        lignes.append(f"  {sans_limite} pod(s) sans limite mémoire déclarée")
    return "Pression mémoire :\n" + "\n".join(lignes)
