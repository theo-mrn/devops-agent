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

from devops_agent.agent import overview, tools

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
    pod: str
    namespace: str
    etat: str
    gravite: str
    redemarrages: int
    etat_precedent: str | None = None
    date: float = field(default_factory=time.time)

    @property
    def cle(self) -> str:
        """Identité pour la déduplication : même pod, même problème."""
        return f"{self.namespace}/{self.pod}:{self.etat}"

    def __str__(self) -> str:
        transition = f"{self.etat_precedent} → " if self.etat_precedent else ""
        suffixe = f" ({self.redemarrages} redémarrages)" if self.redemarrages else ""
        return f"{self.namespace}/{self.pod}  {transition}{self.etat}{suffixe}"


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

    def traiter(self, pod_json: dict) -> Evenement | None:
        """Analyse un objet pod reçu du watch. Renvoie un événement ou None."""
        self.vus += 1

        meta = pod_json.get("metadata", {})
        nom, ns = meta.get("name"), meta.get("namespace")
        if not nom or not ns:
            return None

        identite = f"{ns}/{nom}"
        etat, redemarrages = overview._etat_pod(pod_json)
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

        gravite = _gravite(etat)
        if gravite is None:
            # Retour à la normale : utile à savoir, mais rien à diagnostiquer.
            if precedent and _gravite(precedent[0]):
                self._log(f"  \033[32m✓\033[0m {identite} rétabli ({precedent[0]} → {etat})")
                self.derniere_alerte.pop(f"{identite}:{precedent[0]}", None)
            return None

        ev = Evenement(
            pod=nom, namespace=ns, etat=etat, gravite=gravite,
            redemarrages=redemarrages,
            etat_precedent=precedent[0] if precedent else None,
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

    def suivre(self, sur_evenement=None, duree_max: int | None = None) -> None:
        """Ouvre le watch et traite les événements jusqu'à interruption.

        `sur_evenement` est appelé pour chaque événement retenu. Sans lui,
        les événements sont seulement affichés — mode observation, utile
        pour régler les seuils avant de brancher l'agent.
        """
        if not shutil.which("kubectl"):
            print("[kubectl absent — impossible de surveiller]", file=sys.stderr)
            return

        commande = "get pods"
        if tools.NAMESPACES_AUTORISES:
            # Le watch ne prend qu'un namespace à la fois ; avec une
            # restriction on surveille le premier et on avertit.
            ns = sorted(tools.NAMESPACES_AUTORISES)
            commande += f" -n {ns[0]}"
            if len(ns) > 1:
                self._log(f"\033[33m  surveillance limitée à « {ns[0] }» "
                          f"(watch mono-namespace)\033[0m")
        else:
            commande += " --all-namespaces"

        if tools.verifier_commande(commande) is not None:
            print("[commande refusée par les garde-fous]", file=sys.stderr)
            return

        self._log(f"\033[1m SURVEILLANCE \033[0m {commande}")
        self._log(f"\033[2m  seuil {SEUIL_REDEMARRAGES} redémarrages/jour · "
                  f"dédup {FENETRE_DEDUP // 60} min · Ctrl+C pour arrêter\033[0m")

        processus = subprocess.Popen(
            ["kubectl", *commande.split(), "--watch", "-o", "json"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
        )

        debut = time.time()
        tampon = ""
        profondeur = 0

        # `for ligne in stdout` bloque tant qu'aucune ligne n'arrive : sur
        # un cluster stable, la boucle ne reprendrait jamais la main et
        # `duree_max` ne serait jamais évalué. On lit donc dans un thread
        # et on consomme via une file.
        import queue
        import threading

        lignes = queue.Queue()

        def lire():
            for l in processus.stdout:
                lignes.put(l)
            lignes.put(None)

        threading.Thread(target=lire, daemon=True).start()
        dernier_signe = 0.0
        inventaire_annonce = False

        try:
            while True:
                try:
                    ligne = lignes.get(timeout=1.0)
                except queue.Empty:
                    # Rien reçu : c'est le cas normal sur un cluster sain.
                    if duree_max and time.time() - debut > duree_max:
                        break
                    # Ligne d'attente réécrite sur place (retour chariot,
                    # sans saut de ligne) : le journal des événements
                    # défile proprement au-dessus.
                    #
                    # Hors terminal (redirection, service, conteneur), la
                    # réécriture n'a pas de sens : chaque ligne s'empile.
                    # On l'espace alors très largement.
                    intervalle = 1 if _interactif() else 300
                    if self.verbeux and time.time() - dernier_signe > intervalle:
                        ecoule = int(time.time() - debut)
                        h, m, s = ecoule // 3600, (ecoule % 3600) // 60, ecoule % 60
                        duree = f"{h}h{m:02d}m" if h else f"{m}m{s:02d}s"
                        if _interactif():
                            print(
                                f"\r\033[2m  en attente d'événement… ({duree})"
                                f"\033[K\033[0m",
                                end="", flush=True,
                            )
                        else:
                            print(f"  en attente d'événement… ({duree})", flush=True)
                        dernier_signe = time.time()
                    continue

                if ligne is None:      # le processus s'est terminé
                    break

                # Une fois l'inventaire passé, on bascule en mode
                # « changements » : ce qui arrive ensuite est réel.
                if not inventaire_annonce and self.initial > 0:
                    inventaire_annonce = True
                # `kubectl --watch -o json` produit des objets JSON
                # concaténés, pas un tableau : il faut les découper en
                # suivant l'équilibre des accolades.
                tampon += ligne
                profondeur += ligne.count("{") - ligne.count("}")
                if profondeur > 0 or not tampon.strip():
                    continue

                try:
                    objet = json.loads(tampon)
                except json.JSONDecodeError:
                    tampon = ""
                    continue
                tampon = ""

                evenement = self.traiter(objet)
                if evenement:
                    if self.verbeux and _interactif():
                        print("\r\033[K", end="")   # efface la ligne d'attente
                    couleur = "\033[31m" if evenement.gravite == "critique" else "\033[33m"
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
            processus.terminate()
            if self.verbeux and _interactif():
                print("\r\033[K", end="")
            changements = self.vus - self.initial
            self._log(
                f"\n\033[2m  {self.initial} pods inventoriés au démarrage · "
                f"{changements} changement(s) ensuite · "
                f"{self.retenus} anomalie(s) retenue(s)\033[0m"
            )
