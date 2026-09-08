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
        assert s.traiter(pod(raison="CrashLoopBackOff", redemarrages=5)) is not None
        s.traiter(pod(redemarrages=5))                       # rétabli
        # Le rétablissement a purgé la déduplication du problème précédent.
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
    def test_cle_identifie_ressource_objet_et_probleme(self):
        """La clé inclut le type : un service et un pod homonymes sont distincts."""
        ev = Evenement(pod="web", namespace="prod", etat="CrashLoopBackOff",
                       gravite="critique", redemarrages=5, ressource="pod")
        assert ev.cle == "pod:prod/web:CrashLoopBackOff"

    def test_cle_distingue_les_types_de_ressource(self):
        commun = dict(pod="api", namespace="prod", etat="Panne", gravite="grave")
        assert (Evenement(**commun, ressource="pod").cle
                != Evenement(**commun, ressource="service").cle)

    def test_affichage_montre_la_transition(self):
        ev = Evenement(pod="web", namespace="prod", etat="CrashLoopBackOff",
                       gravite="critique", redemarrages=5,
                       etat_precedent="Running")
        rendu = str(ev)
        assert "Running → CrashLoopBackOff" in rendu
        assert "5 redémarrages" in rendu


class TestNamespacesIgnores:
    """L'agent ne doit pas se surveiller lui-même.

    Chaque redémarrage de l'agent fait passer son Deployment par
    « aucun replica prêt » : sans exclusion, il diagnostique son propre
    redémarrage, ce qui coûte de l'argent pour un non-événement.
    """

    def test_namespace_de_l_agent_ignore(self, monkeypatch):
        monkeypatch.setattr(watcher, "IGNORES", {"devops-agent"})
        s = Surveillance(verbeux=False)
        assert s.traiter(pod(nom="agent-1", ns="devops-agent",
                             raison="CrashLoopBackOff", redemarrages=10)) is None

    def test_autres_namespaces_surveilles(self, monkeypatch):
        monkeypatch.setattr(watcher, "IGNORES", {"devops-agent"})
        s = Surveillance(verbeux=False)
        assert s.traiter(pod(nom="web", ns="prod",
                             raison="CrashLoopBackOff", redemarrages=10)) is not None

    def test_liste_configurable(self, monkeypatch):
        monkeypatch.setattr(watcher, "IGNORES", {"devops-agent", "trivy-system"})
        s = Surveillance(verbeux=False)
        assert s.traiter(pod(nom="scan", ns="trivy-system",
                             raison="Failed", redemarrages=5)) is None
