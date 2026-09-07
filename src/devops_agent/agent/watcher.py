"""Surveillance de cluster par événements — aucun sondage, aucune IA.

Le modèle ArgoCD : on ne redemande pas l'état complet toutes les N
secondes, on maintient une connexion `kubectl --watch` qui pousse les
changements, et on ne réagit qu'à ce qui bouge vraiment.

    polling toutes les 60 s   →  ~400 000 tokens/jour envoyés au modèle
    watch + filtrage          →  ~4 000 tokens/jour

Le facteur 100 ne vient pas du watch lui-même (gratuit dans les deux cas)
mais du FILTRAGE : un cluster pousse des centaines d'événements par
minute — heartbeats, mises à jour de statut, changements d'annotations.
Presque tous sont sans intérêt.

Ce module ne remonte qu'une chose : les TRANSITIONS vers un état anormal,
dédupliquées. Il ne contient aucun appel au modèle ; c'est le déclencheur,
pas le diagnostiqueur.

Usage :
    devops-agent watch              affiche les événements retenus
    devops-agent watch --diagnose   déclenche l'agent sur chaque événement
"""

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field

from devops_agent.agent import detecteurs, overview, tools

# Un pod doit redémarrer au moins ce nombre de fois avant d'être signalé.
# Un redémarrage isolé est souvent transitoire (rolling update, sonde
# trop stricte au démarrage) : réagir tout de suite coûterait pour rien.
SEUIL_REDEMARRAGES = int(os.environ.get("RAG_SEUIL_REDEMARRAGES", "3"))

# Même pod, même problème : on ne rediagnostique pas avant ce délai.
FENETRE_DEDUP = int(os.environ.get("RAG_DEDUP_SECONDES", "1800"))  # 30 min

# États qui déclenchent un événement, du plus au moins grave.
ETATS_ANORMAUX = {
    "OOMKilled": "critique",
    "CrashLoopBackOff": "critique",
    "ImagePullBackOff": "grave",
    "ErrImagePull": "grave",
    "CreateContainerConfigError": "grave",
    "CreateContainerError": "grave",
    "Failed": "grave",
    "Evicted": "grave",
    "Pending": "surveillance",
    "Unknown": "surveillance",
}

# Un Pending est normal pendant quelques secondes (scheduling, pull
# d'image). On ne le signale que s'il dure.
DUREE_PENDING_TOLEREE = int(os.environ.get("RAG_PENDING_TOLERE", "300"))  # 5 min


def _interactif() -> bool:
    """La sortie est-elle un terminal ?

    Hors terminal — redirection vers un fichier, service systemd,
    conteneur — la réécriture de ligne empile des lignes illisibles au
    lieu de les remplacer.
    """
    return sys.stdout.isatty()


def _gravite(etat: str) -> str | None:
    """Gravité d'un état, ou None s'il est normal.

    La correspondance est par préfixe : `overview` produit des états
    enrichis comme « OOMKilled (précédent) » qu'une égalité stricte
    laisserait passer — précisément le cas qu'on veut attraper.
    """
    for anormal, gravite in ETATS_ANORMAUX.items():
        if etat.startswith(anormal):
            return gravite
    return None


@dataclass
class Evenement:
    """Une transition vers un état anormal, digne d'attention."""
    pod: str                      # nom de la ressource
    namespace: str
    etat: str
    gravite: str
    redemarrages: int = 0
    etat_precedent: str | None = None
    ressource: str = "pod"        # pod, service, deployment, pvc, node…
    date: float = field(default_factory=time.time)

    @property
    def nom(self) -> str:
        """Alias lisible : `pod` porte en fait n'importe quelle ressource."""
        return self.pod

    @property
    def cle(self) -> str:
        """Identité pour la déduplication : même objet, même problème."""
        return f"{self.ressource}:{self.namespace}/{self.pod}:{self.etat}"

    def __str__(self) -> str:
        transition = f"{self.etat_precedent} → " if self.etat_precedent else ""
        suffixe = f" ({self.redemarrages} redémarrages)" if self.redemarrages else ""
        emplacement = (
            f"{self.namespace}/{self.pod}" if self.namespace != "-" else self.pod
        )
        return f"[{self.ressource}] {emplacement}  {transition}{self.etat}{suffixe}"


class Surveillance:
    """Maintient l'état du cluster et n'émet que les vraies transitions."""

    def __init__(self, verbeux: bool = True):
        self.verbeux = verbeux
        self.etats: dict[str, tuple[str, int]] = {}   # pod → (état, redémarrages)
        self.depuis: dict[str, float] = {}            # pod → début de l'état courant
        self.derniere_alerte: dict[str, float] = {}   # clé → date de la dernière alerte
        self.vus = 0          # objets pods reçus, état initial compris
        self.retenus = 0      # anomalies jugées dignes d'un diagnostic
        self.initial = 0      # objets reçus lors de l'inventaire de départ
        self.inventaire = True  # vrai tant qu'on reçoit l'état initial

    # ── Filtrage ─────────────────────────────────────────────────

    def _digne_interet(self, ev: Evenement) -> bool:
        """Décide si l'événement mérite de réveiller l'agent."""
        # Un CrashLoop qui vient de commencer peut être un redémarrage
        # normal : on attend quelques cycles.
        if ev.etat == "CrashLoopBackOff" and ev.redemarrages < SEUIL_REDEMARRAGES:
            return False

        # Un Pending bref est normal (scheduling, pull d'image).
        if ev.etat == "Pending":
            debut = self.depuis.get(f"{ev.namespace}/{ev.pod}", ev.date)
            if ev.date - debut < DUREE_PENDING_TOLEREE:
                return False

        # Déduplication : même pod, même problème, dans la fenêtre.
        derniere = self.derniere_alerte.get(ev.cle)
        if derniere and ev.date - derniere < FENETRE_DEDUP:
            return False

        return True

    def traiter(self, pod_json: dict, ressource: str = "pods") -> Evenement | None:
        """Analyse un objet reçu du watch. Renvoie un événement ou None.

        Le détecteur appliqué dépend du type de ressource : un pod ne se
        juge pas comme un service ou un volume.
        """
        self.vus += 1

        meta = pod_json.get("metadata", {})
        nom = meta.get("name")
        ns = meta.get("namespace", "-")     # les nœuds n'en ont pas
        if not nom:
            return None

        detecteur, libelle = detecteurs.DETECTEURS.get(ressource, (None, ressource))
        if detecteur is None:
            return None

        identite = f"{ressource}:{ns}/{nom}"
        verdict = detecteur(pod_json)
        etat = verdict[0] if verdict else "Sain"
        redemarrages = (
            overview._etat_pod(pod_json)[1] if ressource == "pods" else 0
        )
        precedent = self.etats.get(identite)

        # `kubectl --watch` envoie d'abord l'état de tous les pods, puis
        # les changements. Le premier passage est un inventaire, pas des
        # événements : le distinguer évite de croire qu'il se passe
        # quelque chose alors que le cluster est stable.
        if precedent is None and self.inventaire:
            self.initial += 1

        # Rien n'a changé : le cas le plus fréquent, et le moins cher.
        if precedent == (etat, redemarrages):
            return None

        # Le pod entre dans un nouvel état : on date la transition.
        if not precedent or precedent[0] != etat:
            self.depuis[identite] = time.time()

        self.etats[identite] = (etat, redemarrages)

        if verdict is None:
            # Retour à la normale : utile à savoir, mais rien à diagnostiquer.
            if precedent and precedent[0] != "Sain":
                horodatage = time.strftime("%H:%M:%S")
                self._log(f"  \033[2m{horodatage}\033[0m  \033[32m✓ "
                          f"[{libelle}] {ns}/{nom} rétabli\033[0m")
                # La clé stockée utilise le LIBELLÉ (« pod »), pas le nom
                # kubectl (« pods ») : les deux doivent correspondre, sinon
                # la déduplication n'est jamais purgée et un objet réparé
                # puis recassé ne redéclenche pas.
                self.derniere_alerte.pop(
                    f"{libelle}:{ns}/{nom}:{precedent[0]}", None
                )
            return None

        ev = Evenement(
            pod=nom, namespace=ns, etat=etat, gravite=verdict[1],
            redemarrages=redemarrages, ressource=libelle,
            etat_precedent=precedent[0] if precedent and precedent[0] != "Sain" else None,
        )

        if not self._digne_interet(ev):
            return None

        self.derniere_alerte[ev.cle] = ev.date
        self.retenus += 1
        return ev

    # ── Boucle de surveillance ───────────────────────────────────

    def _log(self, texte: str) -> None:
        if self.verbeux:
            print(texte, flush=True)

    def suivre(self, sur_evenement=None, duree_max: int | None = None,
               ressources: list[str] | None = None) -> None:
        """Ouvre un watch par ressource et traite les événements.

        Une seule connexion ne suffit pas : `kubectl --watch` ne surveille
        qu'un type de ressource à la fois. Un pod sain ne garantit pas un
        service joignable ni un volume lié — chaque type a ses pannes.

        `sur_evenement` est appelé pour chaque anomalie retenue. Sans lui,
        les événements sont seulement affichés : mode observation, utile
        pour régler les seuils avant de brancher l'agent.
        """
        import queue
        import threading

        if not shutil.which("kubectl"):
            print("[kubectl absent — impossible de surveiller]", file=sys.stderr)
            return

        ressources = ressources or detecteurs.PAR_DEFAUT
        portee = ""
        if tools.NAMESPACES_AUTORISES:
            ns = sorted(tools.NAMESPACES_AUTORISES)
            portee = f" -n {ns[0]}"
            if len(ns) > 1:
                self._log(f"\033[33m  surveillance limitée à « {ns[0]} » "
                          f"(watch mono-namespace)\033[0m")
        else:
            portee = " --all-namespaces"

        self._log(f"\033[1m SURVEILLANCE \033[0m {', '.join(ressources)}")
        self._log(f"\033[2m  seuil {SEUIL_REDEMARRAGES} redémarrages/jour · "
                  f"dédup {FENETRE_DEDUP // 60} min · Ctrl+C pour arrêter\033[0m")

        # Une file partagée reçoit (ressource, ligne) depuis tous les
        # watchs ; la boucle principale garde ainsi la main et peut
        # s'arrêter proprement même si le cluster est muet.
        lignes: queue.Queue = queue.Queue()
        processus: list[subprocess.Popen] = []

        def surveiller(ressource: str) -> None:
            # Les nœuds ne vivent pas dans un namespace.
            cible = "" if ressource == "nodes" else portee
            commande = f"get {ressource}{cible}"
            if tools.verifier_commande(commande) is not None:
                return
            proc = subprocess.Popen(
                ["kubectl", *commande.split(), "--watch", "-o", "json"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, bufsize=1,
            )
            processus.append(proc)
            tampon, profondeur = "", 0
            for ligne in proc.stdout:
                tampon += ligne
                profondeur += ligne.count("{") - ligne.count("}")
                if profondeur > 0 or not tampon.strip():
                    continue
                try:
                    lignes.put((ressource, json.loads(tampon)))
                except json.JSONDecodeError:
                    pass
                tampon = ""

        for ressource in ressources:
            threading.Thread(target=surveiller, args=(ressource,),
                             daemon=True).start()

        debut = time.time()
        dernier_signe = 0.0

        try:
            while True:
                try:
                    ressource, objet = lignes.get(timeout=1.0)
                except queue.Empty:
                    if duree_max and time.time() - debut > duree_max:
                        break
                    intervalle = 1 if _interactif() else 300
                    if self.verbeux and time.time() - dernier_signe > intervalle:
                        ecoule = int(time.time() - debut)
                        h, m, s = ecoule // 3600, (ecoule % 3600) // 60, ecoule % 60
                        duree = f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"
                        if _interactif():
                            print(f"\r\033[2m  en attente d'événement… ({duree})"
                                  f"\033[K\033[0m", end="", flush=True)
                        else:
                            print(f"  en attente d'événement… ({duree})", flush=True)
                        dernier_signe = time.time()
                    continue

                evenement = self.traiter(objet, ressource)
                if evenement:
                    if self.verbeux and _interactif():
                        print("\r\033[K", end="")
                    couleur = ("\033[31m" if evenement.gravite == "critique"
                               else "\033[33m")
                    horodatage = time.strftime("%H:%M:%S")
                    self._log(f"  \033[2m{horodatage}\033[0m  "
                              f"{couleur}{evenement}\033[0m")
                    if sur_evenement:
                        sur_evenement(evenement)

                if duree_max and time.time() - debut > duree_max:
                    break

        except KeyboardInterrupt:
            pass
        finally:
            for proc in processus:
                proc.terminate()
            if self.verbeux and _interactif():
                print("\r\033[K", end="")
            changements = self.vus - self.initial
            self._log(
                f"\n\033[2m  {self.initial} objets inventoriés au démarrage · "
                f"{changements} changement(s) ensuite · "
                f"{self.retenus} anomalie(s) retenue(s)\033[0m"
            )
