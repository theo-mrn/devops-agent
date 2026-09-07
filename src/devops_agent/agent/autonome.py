"""Mode autonome : détecter, décider, diagnostiquer — sans intervention.

C'est la finalité de l'outil. On le lance une fois, il tourne, et il
produit un diagnostic quand quelque chose casse.

    watcher (gratuit)  →  anomalie  →  agent (payant)  →  rapport

Le watcher fait le tri : sur un cluster réel, 98 % des événements sont du
bruit. Seul ce qui passe les filtres déclenche un diagnostic, et chaque
diagnostic est plafonné par un budget.

La question posée à l'agent est CONSTRUITE à partir de l'événement — c'est
là qu'un outil autonome se distingue d'un assistant : personne ne rédige
de prompt.

Usage :
    devops-agent auto                    surveille et diagnostique
    devops-agent auto --budget-jour 2.0  plafond quotidien
    devops-agent auto --dry-run          montre sans dépenser
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path

from devops_agent.agent.watcher import Evenement, Surveillance
from devops_agent.core import config

# Plafond de dépense sur 24 h glissantes. Au-delà, l'outil continue de
# surveiller mais cesse de diagnostiquer : mieux vaut une alerte brute
# qu'une facture non maîtrisée.
BUDGET_JOUR = float(os.environ.get("RAG_BUDGET_JOUR", "5.0"))

JOURNAL = Path(os.environ.get("RAG_JOURNAL", "data/diagnostics.jsonl"))

# Comment interroger l'agent selon le symptôme. Un diagnostic pertinent
# commence par une bonne question : elle oriente sans imposer la réponse.
QUESTIONS = {
    "OOMKilled": (
        "Le pod {ns}/{pod} a été tué pour dépassement mémoire (OOMKilled), "
        "{n} redémarrage(s). Établis la cause : la limite est-elle trop basse "
        "pour la charge, ou l'application fuit-elle ? Compare la limite "
        "configurée à la consommation observée, et regarde si le schéma se "
        "répète après chaque redémarrage."
    ),
    "CrashLoopBackOff": (
        "Le pod {ns}/{pod} redémarre en boucle ({n} redémarrages). Trouve la "
        "cause : lis les logs de l'instance précédente, vérifie le code de "
        "sortie dans Last State, et écarte les causes courantes — sonde de "
        "vivacité trop stricte, dépassement mémoire, commande introuvable."
    ),
    "ImagePullBackOff": (
        "Le pod {ns}/{pod} ne parvient pas à télécharger son image. Identifie "
        "laquelle des trois causes s'applique : tag inexistant, authentification "
        "au registre, ou problème réseau depuis le nœud."
    ),
    "ErrImagePull": (
        "Le pod {ns}/{pod} échoue au téléchargement de son image. Identifie la "
        "cause exacte dans les événements du pod."
    ),
    "CreateContainerConfigError": (
        "Le pod {ns}/{pod} ne démarre pas : une ConfigMap ou un Secret "
        "référencé est probablement absent. Identifie lequel."
    ),
    "Pending": (
        "Le pod {ns}/{pod} reste en attente de planification. Détermine ce qui "
        "l'empêche : ressources insuffisantes sur les nœuds, taint non toléré, "
        "sélecteur sans correspondance, ou volume non lié."
    ),
    "Evicted": (
        "Le pod {ns}/{pod} a été expulsé de son nœud. Détermine quelle "
        "ressource manquait sur le nœud et si la classe QoS du pod l'a rendu "
        "prioritaire à l'expulsion."
    ),
}

QUESTION_PAR_DEFAUT = (
    "Le pod {ns}/{pod} est en état {etat} ({n} redémarrages). Diagnostique la "
    "cause et propose une correction à valider par un humain."
)


def _question(ev: Evenement) -> str:
    """Construit la question adaptée au symptôme observé."""
    for symptome, gabarit in QUESTIONS.items():
        if ev.etat.startswith(symptome):
            return gabarit.format(ns=ev.namespace, pod=ev.pod, n=ev.redemarrages)
    return QUESTION_PAR_DEFAUT.format(
        ns=ev.namespace, pod=ev.pod, etat=ev.etat, n=ev.redemarrages
    )


class Autonome:
    """Surveille et diagnostique, sous contrainte de budget."""

    def __init__(self, budget_jour: float = BUDGET_JOUR,
                 dry_run: bool = False, modele: str | None = None):
        self.budget_jour = budget_jour
        self.dry_run = dry_run
        if modele:
            config.LLM_API = modele
        self.depenses: list[tuple[float, float]] = []   # (date, montant)
        self.diagnostics = 0

    # ── Budget ───────────────────────────────────────────────────

    def _depense_24h(self) -> float:
        limite = time.time() - 86400
        self.depenses = [(d, m) for d, m in self.depenses if d > limite]
        return sum(m for _, m in self.depenses)

    def _budget_disponible(self) -> bool:
        return self._depense_24h() < self.budget_jour

    # ── Journal ──────────────────────────────────────────────────

    def _consigner(self, ev: Evenement, reponse: str, cout: float,
                   trace: list[dict]) -> None:
        """Écrit le diagnostic sur disque : il doit survivre au processus."""
        JOURNAL.parent.mkdir(parents=True, exist_ok=True)
        entree = {
            "date": datetime.now().isoformat(timespec="seconds"),
            "namespace": ev.namespace,
            "pod": ev.pod,
            "etat": ev.etat,
            "gravite": ev.gravite,
            "redemarrages": ev.redemarrages,
            "modele": config.LLM_API,
            "cout": round(cout, 5),
            "outils": [a["outil"] for a in trace],
            "diagnostic": reponse,
        }
        with JOURNAL.open("a") as f:
            f.write(json.dumps(entree, ensure_ascii=False) + "\n")

    # ── Traitement d'une anomalie ────────────────────────────────

    def traiter(self, ev: Evenement) -> None:
        question = _question(ev)

        if self.dry_run:
            print(f"\n\033[2m  [simulation] question qui serait posée :\033[0m")
            print(f"\033[2m  {question}\033[0m\n")
            return

        if not self._budget_disponible():
            print(
                f"\n\033[33m  budget quotidien de {self.budget_jour} $ atteint — "
                f"anomalie signalée sans diagnostic :\033[0m\n  {ev}\n"
            )
            return

        from devops_agent.agent.loop import Agent

        print(f"\n\033[1m  DIAGNOSTIC \033[0m {ev}\n")
        agent = Agent()
        reponse = agent.demander(question)
        cout = agent.cout()

        self.depenses.append((time.time(), cout))
        self.diagnostics += 1
        self._consigner(ev, reponse, cout, agent.trace)

        print(f"\n{reponse}\n")
        print(
            f"\033[2m  {cout:.4f} $ · "
            f"{self._depense_24h():.2f}/{self.budget_jour} $ sur 24 h\033[0m\n"
        )

    # ── Boucle ───────────────────────────────────────────────────

    def demarrer(self, duree_max: int | None = None) -> None:
        mode = "simulation" if self.dry_run else config.LLM_API
        print(f"\033[1m MODE AUTONOME \033[0m {mode}")
        print(f"\033[2m  budget {self.budget_jour} $/jour · "
              f"journal {JOURNAL}\033[0m")

        Surveillance().suivre(sur_evenement=self.traiter, duree_max=duree_max)

        if self.diagnostics:
            print(f"\033[2m  {self.diagnostics} diagnostic(s) · "
                  f"{self._depense_24h():.2f} $ dépensés\033[0m")
