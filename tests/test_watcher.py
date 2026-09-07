"""Filtrage des événements — c'est lui qui détermine la facture API.

Un cluster pousse des centaines d'événements par minute. Chaque événement
retenu à tort déclenche un diagnostic payant ; chaque événement manqué est
une panne non détectée.
"""

import time

import pytest

from devops_agent.agent import watcher
from devops_agent.agent.watcher import Evenement, Surveillance


def pod(nom="web-1", ns="prod", phase="Running", raison=None,
        redemarrages=0, oom_precedent=False):
    """Construit un objet pod tel que le renvoie l'API Kubernetes."""
    etat = {"waiting": {"reason": raison}} if raison else {"running": {}}
    conteneur = {"restartCount": redemarrages, "state": etat}
    if oom_precedent:
        conteneur["lastState"] = {"terminated": {"reason": "OOMKilled"}}
    return {
        "metadata": {"name": nom, "namespace": ns},
        "status": {"phase": phase, "containerStatuses": [conteneur]},
    }


class TestFiltrage:
    """Ce qui doit passer, et surtout ce qui ne doit pas."""

    def test_pod_sain_ignore(self):
        s = Surveillance(verbeux=False)
        assert s.traiter(pod()) is None

    def test_etat_inchange_ignore(self):
        """Le cas le plus fréquent : un heartbeat sans changement."""
        s = Surveillance(verbeux=False)
        p = pod(raison="CrashLoopBackOff", redemarrages=5)
        assert s.traiter(p) is not None      # première fois
        assert s.traiter(p) is None          # identique : rien à signaler

    def test_crashloop_sous_le_seuil_ignore(self):
        """Un redémarrage isolé est souvent transitoire."""
        s = Surveillance(verbeux=False)
        assert s.traiter(pod(raison="CrashLoopBackOff", redemarrages=1)) is None

    def test_crashloop_au_dessus_du_seuil_retenu(self):
        s = Surveillance(verbeux=False)
        ev = s.traiter(pod(raison="CrashLoopBackOff",
                           redemarrages=watcher.SEUIL_REDEMARRAGES))
        assert ev is not None
        assert ev.gravite == "critique"

    def test_imagepullbackoff_retenu_immediatement(self):
        """Pas de seuil : une image introuvable ne se corrige pas seule."""
        s = Surveillance(verbeux=False)
        ev = s.traiter(pod(raison="ImagePullBackOff"))
        assert ev is not None and ev.gravite == "grave"

    def test_pending_bref_ignore(self):
        """Un Pending de quelques secondes est du scheduling normal."""
        s = Surveillance(verbeux=False)
        assert s.traiter(pod(phase="Pending")) is None

    def test_pending_durable_retenu(self):
        s = Surveillance(verbeux=False)
        s.traiter(pod(phase="Pending"))
        # Le pod attend depuis longtemps ; son compteur de redémarrages
        # change, ce qui provoque une nouvelle évaluation.
        s.depuis["prod/web-1"] = time.time() - watcher.DUREE_PENDING_TOLEREE - 10
        assert s.traiter(pod(phase="Pending", redemarrages=1)) is not None

    def test_oomkilled_precedent_detecte(self):
        """Le pod tourne, mais il a été tué au cycle précédent."""
        s = Surveillance(verbeux=False)
        ev = s.traiter(pod(redemarrages=4, oom_precedent=True))
        assert ev is not None and "OOMKilled" in ev.etat


class TestDeduplication:
    """Un pod qui redémarre 12 fois ne doit pas coûter 12 diagnostics."""

    def test_meme_probleme_non_rediagnostique(self):
        s = Surveillance(verbeux=False)
        assert s.traiter(pod(raison="CrashLoopBackOff", redemarrages=5)) is not None
        # Le compteur monte, mais c'est le même problème.
        assert s.traiter(pod(raison="CrashLoopBackOff", redemarrages=6)) is None
        assert s.traiter(pod(raison="CrashLoopBackOff", redemarrages=7)) is None

    def test_apres_la_fenetre_rediagnostique(self):
        s = Surveillance(verbeux=False)
        s.traiter(pod(raison="CrashLoopBackOff", redemarrages=5))
        # On recule la dernière alerte au-delà de la fenêtre.
        for cle in s.derniere_alerte:
            s.derniere_alerte[cle] -= watcher.FENETRE_DEDUP + 1
        assert s.traiter(pod(raison="CrashLoopBackOff", redemarrages=6)) is not None

    def test_probleme_different_meme_pod_retenu(self):
        s = Surveillance(verbeux=False)
        s.traiter(pod(raison="CrashLoopBackOff", redemarrages=5))
        # Nouveau problème : mérite un nouveau diagnostic.
        assert s.traiter(pod(raison="ImagePullBackOff", redemarrages=5)) is not None

    def test_retablissement_libere_la_dedup(self):
        """Un pod réparé puis recassé doit redéclencher."""
        s = Surveillance(verbeux=False)
        s.traiter(pod(raison="CrashLoopBackOff", redemarrages=5))
        s.traiter(pod(redemarrages=5))                       # rétabli
        assert s.traiter(pod(raison="CrashLoopBackOff", redemarrages=6)) is not None


class TestVolume:
    """Le point du module : ne pas payer pour du bruit."""

    def test_bruit_massif_produit_peu_d_evenements(self):
        s = Surveillance(verbeux=False)
        # 500 pods sains qui envoient chacun 10 heartbeats.
        for tour in range(10):
            for i in range(500):
                s.traiter(pod(nom=f"web-{i}"))
        assert s.vus == 5000
        assert s.retenus == 0, "des pods sains ont déclenché des alertes"

    def test_une_panne_dans_le_bruit(self):
        s = Surveillance(verbeux=False)
        for i in range(200):
            s.traiter(pod(nom=f"web-{i}"))
        s.traiter(pod(nom="payment", raison="CrashLoopBackOff", redemarrages=8))
        for i in range(200):
            s.traiter(pod(nom=f"web-{i}"))
        assert s.retenus == 1


class TestEvenement:
    def test_cle_identifie_pod_et_probleme(self):
        ev = Evenement(pod="web", namespace="prod", etat="CrashLoopBackOff",
                       gravite="critique", redemarrages=5)
        assert ev.cle == "prod/web:CrashLoopBackOff"

    def test_affichage_montre_la_transition(self):
        ev = Evenement(pod="web", namespace="prod", etat="CrashLoopBackOff",
                       gravite="critique", redemarrages=5,
                       etat_precedent="Running")
        rendu = str(ev)
        assert "Running → CrashLoopBackOff" in rendu
        assert "5 redémarrages" in rendu
