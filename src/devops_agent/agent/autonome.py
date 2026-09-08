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

from devops_agent.agent import verification, webhook
from devops_agent.agent.correlation import Groupe, Tampon
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
    "SansEndpoint": (
        "Le service {ns}/{pod} n'a aucun endpoint : il ne route vers aucun pod, "
        "donc l'application est injoignable même si les pods tournent. Compare "
        "le sélecteur du Service aux labels des pods du namespace, et vérifie "
        "que ces pods passent bien leur readiness probe."
    ),
    "EndpointsNonPrets": (
        "Le service {ns}/{pod} a des endpoints, mais aucun n'est prêt : les pods "
        "existent et échouent leur readiness probe. Identifie pourquoi la sonde "
        "échoue."
    ),
    "RolloutBloque": (
        "Le déploiement {ns}/{pod} a un rollout bloqué (ProgressDeadlineExceeded). "
        "Détermine ce qui empêche les nouveaux pods de devenir prêts."
    ),
    "AucunReplicaPret": (
        "Le déploiement {ns}/{pod} n'a aucun replica prêt : le service est "
        "totalement indisponible. Trouve ce qui empêche les pods de démarrer."
    ),
    "ReplicasIncomplets": (
        "Le déploiement {ns}/{pod} n'atteint pas le nombre de replicas voulu. "
        "Détermine ce qui bloque les pods manquants : ressources insuffisantes, "
        "contrainte de planification, ou échec de démarrage."
    ),
    "VolumeNonLie": (
        "Le PersistentVolumeClaim {ns}/{pod} reste en attente : aucun volume ne "
        "lui a été attribué. Vérifie la StorageClass demandée, sa disponibilité, "
        "et si le provisionnement dynamique est actif."
    ),
    "VolumePerdu": (
        "Le PersistentVolumeClaim {ns}/{pod} a perdu son volume. Évalue "
        "l'étendue de la perte de données et les options de restauration."
    ),
    "NoeudNotReady": (
        "Le nœud {pod} est NotReady : tout ce qu'il porte est en danger. "
        "Identifie la cause — kubelet arrêté, perte réseau, ou épuisement de "
        "ressources — et liste les charges affectées."
    ),
    "PressionMemoire": (
        "Le nœud {pod} est sous pression mémoire et va commencer à expulser des "
        "pods. Identifie ce qui consomme la mémoire et quels pods sont menacés "
        "en premier selon leur classe QoS."
    ),
    "PressionDisque": (
        "Le nœud {pod} est sous pression disque. Identifie ce qui remplit le "
        "disque — images non nettoyées, logs, volumes éphémères."
    ),
    "JobEchoue": (
        "Le Job {ns}/{pod} a définitivement échoué. Lis les logs de ses pods "
        "pour établir la cause."
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


def _question_groupe(groupe: Groupe) -> str:
    """Construit une question unique pour des anomalies corrélées.

    Un PVC non lié produit un pod Pending, qui vide les endpoints du
    service : trois symptômes, une cause. Les diagnostiquer séparément
    coûte trois fois plus et donne trois rapports partiels.
    """
    if len(groupe.evenements) == 1:
        return _question(groupe.evenements[0])

    symptomes = "\n".join(
        f"  - [{e.ressource}] {e.namespace}/{e.pod} : {e.etat}"
        + (f" ({e.redemarrages} redémarrages)" if e.redemarrages else "")
        for e in groupe.evenements
    )
    return (
        f"Plusieurs anomalies sont apparues simultanément dans le namespace "
        f"{groupe.namespace} :\n\n{symptomes}\n\n"
        "Ces symptômes sont probablement liés. Établis la CAUSE RACINE unique "
        "qui les explique, plutôt que de traiter chaque symptôme séparément — "
        "un volume non lié bloque un pod, qui vide les endpoints d'un service, "
        "qui fait échouer un déploiement.\n\n"
        "Indique lequel de ces symptômes est la cause et lesquels sont des "
        "conséquences, puis propose le correctif qui traite la cause."
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

    def _consigner(self, groupe: Groupe, reponse: str, cout: float,
                   trace: list[dict]) -> None:
        """Écrit le diagnostic sur disque : il doit survivre au processus."""
        JOURNAL.parent.mkdir(parents=True, exist_ok=True)
        entree = {
            "date": datetime.now().isoformat(timespec="seconds"),
            "namespace": groupe.namespace,
            "gravite": groupe.gravite,
            "anomalies": [
                {"ressource": e.ressource, "nom": e.pod,
                 "namespace": e.namespace, "etat": e.etat,
                 "redemarrages": e.redemarrages}
                for e in groupe.evenements
            ],
            "modele": config.LLM_API,
            "cout": round(cout, 5),
            "outils": [a["outil"] for a in trace],
            "diagnostic": reponse,
        }
        with JOURNAL.open("a") as f:
            f.write(json.dumps(entree, ensure_ascii=False) + "\n")

    # ── Traitement d'une anomalie ────────────────────────────────

    def traiter(self, groupe: Groupe) -> None:
        """Diagnostique un groupe d'anomalies corrélées, en un seul appel."""
        question = _question_groupe(groupe)
        ev = groupe.evenements[0]

        if self.dry_run:
            print(f"\n\033[2m  [simulation] question qui serait posée :\033[0m")
            print(f"\033[2m  {question}\033[0m\n")
            return

        if not self._budget_disponible():
            print(
                f"\n\033[33m  budget quotidien de {self.budget_jour} $ atteint — "
                f"anomalie signalée sans diagnostic :\033[0m\n  {groupe.resume()}\n"
            )
            return

        from devops_agent.agent.loop import Agent

        print(f"\n\033[1m  DIAGNOSTIC \033[0m {groupe.resume()}\n")
        agent = Agent()
        reponse = agent.demander(question)
        cout = agent.cout()

        self.depenses.append((time.time(), cout))
        self.diagnostics += 1
        self._consigner(groupe, reponse, cout, agent.trace)

        # Vérification mécanique : confronter les affirmations du
        # rapport à l'état réel du cluster. Aucun appel au modèle, donc
        # aucun surcoût — et c'est ce qui attrape les erreurs de FAIT,
        # le risque principal d'un agent de diagnostic.
        controles = []
        for e in groupe.evenements:
            controles += verification.verifier(
                reponse, e.ressource, e.pod, e.namespace
            )
            if e.ressource == "service":
                selecteur = verification.verifier_selecteur(e.pod, e.namespace)
                if selecteur:
                    controles.append(selecteur)

        print(f"\n{reponse}")
        if controles:
            print(verification.formater(controles))

        # Envoi au webhook : un rapport qui reste dans les logs du pod
        # n'est lu par personne. L'échec n'interrompt rien — le
        # diagnostic est déjà dans le journal local.
        if webhook.actif():
            envoye = webhook.notifier(
                groupe, reponse, cout,
                [a["outil"] for a in agent.trace],
                [str(c) for c in controles],
            )
            if envoye:
                print("\033[2m  rapport transmis au webhook\033[0m")
        print()
        print(
            f"\033[2m  {cout:.4f} $ · "
            f"{self._depense_24h():.2f}/{self.budget_jour} $ sur 24 h\033[0m\n"
        )

    # ── Boucle ───────────────────────────────────────────────────

    def demarrer(self, duree_max: int | None = None) -> None:
        from devops_agent.agent.correlation import FENETRE

        mode = "simulation" if self.dry_run else config.LLM_API
        print(f"\033[1m MODE AUTONOME \033[0m {mode}")
        print(f"\033[2m  budget {self.budget_jour} $/jour · "
              f"corrélation {FENETRE:.0f}s · journal {JOURNAL}\033[0m")

        tampon = Tampon()

        def accumuler(ev: Evenement) -> None:
            # On n'appelle pas l'agent tout de suite : une panne se
            # propage en quelques secondes, et les symptômes qui suivent
            # appartiennent souvent au même incident.
            tampon.ajouter(ev)

        def livrer() -> None:
            # Appelé à chaque seconde d'inactivité : sans cela, un groupe
            # dont la fenêtre est écoulée attendrait le prochain
            # événement pour être traité.
            for groupe in tampon.prets():
                self.traiter(groupe)

        Surveillance().suivre(
            sur_evenement=accumuler, sur_attente=livrer, duree_max=duree_max
        )

        # À l'arrêt, ne pas perdre ce qui attendait encore.
        for groupe in tampon.vider():
            self.traiter(groupe)

        if self.diagnostics:
            print(f"\033[2m  {self.diagnostics} diagnostic(s) · "
                  f"{self._depense_24h():.2f} $ dépensés\033[0m")
