"""Regroupement d'anomalies corrélées.

Une panne se manifeste rarement seule. Un PersistentVolumeClaim qui ne se
lie pas produit un pod en Pending, qui vide les endpoints du service, qui
fait échouer le déploiement — quatre anomalies, une seule cause.

Sans regroupement, chacune déclenche son propre agent : quatre
diagnostics qui répètent la même investigation, quatre fois le coût, et
quatre rapports partiels au lieu d'un complet.

Ce module accumule les anomalies pendant quelques secondes, les regroupe
par proximité, et ne réveille l'agent qu'une fois par groupe.

Aucun appel au modèle ici : le regroupement est déterministe.
"""

import os
import re
import time
from dataclasses import dataclass, field

from devops_agent.agent.watcher import Evenement

# Délai d'accumulation avant traitement. Une panne se propage en
# quelques secondes : trop court, on rate la corrélation ; trop long, le
# diagnostic arrive en retard.
FENETRE = float(os.environ.get("RAG_FENETRE_CORRELATION", "8.0"))

# Au-delà, on traite sans attendre : un cluster qui s'effondre ne doit
# pas retarder le premier diagnostic.
MAX_PAR_GROUPE = int(os.environ.get("RAG_MAX_GROUPE", "12"))


def _racine(nom: str) -> str:
    """Nom de la charge, débarrassé des suffixes générés.

    Kubernetes suffixe les pods d'un ReplicaSet (`web-7d9f8b-x2k4p`) et
    les membres d'un StatefulSet (`postgres-0`). Retirer ces suffixes
    rapproche un pod de son service et de son déploiement.
    """
    nom = re.sub(r"-[a-z0-9]{8,10}-[a-z0-9]{5}$", "", nom)   # ReplicaSet
    nom = re.sub(r"-\d+$", "", nom)                          # StatefulSet
    nom = re.sub(r"-[a-z0-9]{5}$", "", nom)                  # Job / DaemonSet
    return nom


@dataclass
class Groupe:
    """Un ensemble d'anomalies probablement liées."""
    evenements: list[Evenement] = field(default_factory=list)
    premiere: float = field(default_factory=time.time)

    @property
    def namespace(self) -> str:
        return self.evenements[0].namespace if self.evenements else "-"

    @property
    def gravite(self) -> str:
        """La gravité du groupe est celle de son élément le plus grave."""
        ordre = {"critique": 3, "grave": 2, "surveillance": 1}
        return max(
            (e.gravite for e in self.evenements),
            key=lambda g: ordre.get(g, 0),
            default="surveillance",
        )

    def accepte(self, ev: Evenement) -> bool:
        """L'anomalie appartient-elle à ce groupe ?

        Deux critères : même namespace, et racine de nom commune. Un nœud
        en difficulté rejoint tout groupe, puisqu'il affecte tout ce qu'il
        porte.
        """
        if not self.evenements:
            return True
        if len(self.evenements) >= MAX_PAR_GROUPE:
            return False

        # Un problème de nœud explique potentiellement tout le reste.
        if ev.ressource == "node" or any(e.ressource == "node" for e in self.evenements):
            return True

        if ev.namespace != self.namespace:
            return False

        racine = _racine(ev.pod)
        return any(
            _racine(e.pod) == racine
            or racine in _racine(e.pod)
            or _racine(e.pod) in racine
            for e in self.evenements
        )

    def resume(self) -> str:
        """Description compacte du groupe, pour l'affichage."""
        if len(self.evenements) == 1:
            return str(self.evenements[0])
        types = ", ".join(
            f"{e.ressource}/{e.pod} ({e.etat})" for e in self.evenements
        )
        return f"{len(self.evenements)} anomalies liées dans {self.namespace} — {types}"


class Tampon:
    """Accumule les anomalies et livre des groupes cohérents.

    Le traitement n'est pas piloté par un minuteur : `pret()` est
    interrogé par la boucle de surveillance, qui repasse chaque seconde.
    C'est suffisant et ça évite un thread de plus.
    """

    def __init__(self, fenetre: float = FENETRE):
        self.fenetre = fenetre
        self.groupes: list[Groupe] = []

    def ajouter(self, ev: Evenement) -> None:
        for groupe in self.groupes:
            if groupe.accepte(ev):
                groupe.evenements.append(ev)
                return
        self.groupes.append(Groupe(evenements=[ev]))

    def prets(self) -> list[Groupe]:
        """Groupes dont la fenêtre d'accumulation est écoulée.

        Un groupe saturé sort immédiatement : sur un cluster qui
        s'effondre, mieux vaut diagnostiquer tout de suite.
        """
        maintenant = time.time()
        murs, en_attente = [], []
        for groupe in self.groupes:
            if (maintenant - groupe.premiere >= self.fenetre
                    or len(groupe.evenements) >= MAX_PAR_GROUPE):
                murs.append(groupe)
            else:
                en_attente.append(groupe)
        self.groupes = en_attente
        return murs

    def vider(self) -> list[Groupe]:
        """Rend tout ce qui reste, sans attendre — à l'arrêt du programme."""
        restants, self.groupes = self.groupes, []
        return restants
