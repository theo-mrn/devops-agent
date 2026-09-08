"""Envoi des rapports de diagnostic vers un webhook.

Sans cela, un rapport vit dans les logs du pod et dans un JSONL sur un
volume : il faut `kubectl exec` pour le lire. Inutilisable au quotidien.

L'agent envoie un POST JSON à une URL configurable. Derrière, n'importe
quoi qui reçoit du HTTP — n8n, Slack, Discord, Teams, un service maison.

    RAG_WEBHOOK_URL=https://n8n.exemple/webhook/diagnostics

Deux exigences de sûreté :

  1. le rapport passe par le sanitizer avant l'envoi — il peut contenir
     des extraits de logs ou de manifests ;
  2. un échec d'envoi ne doit jamais interrompre la surveillance : le
     diagnostic reste dans le journal local.

Écrit avec `urllib` plutôt qu'avec `requests` : une dépendance de moins
dans une image qu'on veut légère.
"""

import json
import os
import time
import urllib.error
import urllib.request

from devops_agent.agent import sanitizer

URL = os.environ.get("RAG_WEBHOOK_URL", "")

# Un en-tête d'authentification optionnel, pour les endpoints protégés :
#   RAG_WEBHOOK_AUTH="Bearer mon-jeton"
AUTH = os.environ.get("RAG_WEBHOOK_AUTH", "")

TIMEOUT = int(os.environ.get("RAG_WEBHOOK_TIMEOUT", "15"))
TENTATIVES = int(os.environ.get("RAG_WEBHOOK_TENTATIVES", "3"))

# Seuls les diagnostics d'au moins cette gravité sont envoyés.
# « surveillance » envoie tout, « critique » uniquement l'urgent.
# Par défaut, seules les anomalies graves et critiques sont transmises.
# « surveillance » regroupe le bruit de fond — jobs éphémères, replicas
# temporairement incomplets — qui n'a pas à encombrer un canal Discord.
GRAVITE_MIN = os.environ.get("RAG_WEBHOOK_GRAVITE", "grave")

_ORDRE = {"surveillance": 1, "grave": 2, "critique": 3}


def actif() -> bool:
    """Un webhook est-il configuré ?"""
    return bool(URL)


def _merite_envoi(gravite: str) -> bool:
    return _ORDRE.get(gravite, 1) >= _ORDRE.get(GRAVITE_MIN, 1)


def construire_charge(groupe, rapport: str, cout: float,
                      outils: list[str], verifications: list[str]) -> dict:
    """Assemble le corps JSON envoyé au webhook.

    Le rapport est assaini : il cite des extraits de logs et de manifests
    qui peuvent contenir des identifiants.
    """
    return {
        "horodatage": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "cluster": os.environ.get("RAG_CLUSTER_NOM", ""),
        "gravite": groupe.gravite,
        "namespace": groupe.namespace,
        "anomalies": [
            {
                "ressource": e.ressource,
                "nom": e.pod,
                "namespace": e.namespace,
                "etat": e.etat,
                "redemarrages": e.redemarrages,
            }
            for e in groupe.evenements
        ],
        "resume": groupe.resume(),
        "diagnostic": sanitizer.masquer(rapport),
        "verifications": [sanitizer.masquer(v) for v in verifications],
        "outils_appeles": outils,
        "cout_dollars": round(cout, 5),
    }


def envoyer(charge: dict) -> bool:
    """Envoie le rapport. Renvoie True si le webhook a accepté.

    Un échec est journalisé mais jamais propagé : la surveillance doit
    continuer même si le destinataire est indisponible, et le diagnostic
    reste dans le journal local.
    """
    if not URL:
        return False

    corps = json.dumps(charge, ensure_ascii=False).encode("utf-8")
    entetes = {
        "Content-Type": "application/json",
        "User-Agent": "devops-agent",
    }
    if AUTH:
        entetes["Authorization"] = AUTH

    for tentative in range(1, TENTATIVES + 1):
        requete = urllib.request.Request(URL, data=corps, headers=entetes,
                                         method="POST")
        try:
            with urllib.request.urlopen(requete, timeout=TIMEOUT) as reponse:
                if 200 <= reponse.status < 300:
                    return True
                motif = f"HTTP {reponse.status}"
        except urllib.error.HTTPError as e:
            motif = f"HTTP {e.code}"
            # Une erreur 4xx ne se corrigera pas en réessayant.
            if 400 <= e.code < 500:
                print(f"\033[33m  webhook refusé ({motif}) — "
                      f"vérifier RAG_WEBHOOK_URL\033[0m", flush=True)
                return False
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            motif = type(e).__name__

        if tentative < TENTATIVES:
            time.sleep(2 ** tentative)   # 2 s, puis 4 s

    print(f"\033[33m  webhook injoignable après {TENTATIVES} tentatives "
          f"({motif}) — le diagnostic reste dans le journal local\033[0m",
          flush=True)
    return False


def notifier(groupe, rapport: str, cout: float, outils: list[str],
             verifications: list[str] | None = None) -> bool:
    """Envoie un diagnostic, si un webhook est configuré et la gravité suffisante."""
    if not actif() or not _merite_envoi(groupe.gravite):
        return False
    charge = construire_charge(groupe, rapport, cout, outils,
                               verifications or [])
    return envoyer(charge)
