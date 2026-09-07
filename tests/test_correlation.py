"""Regroupement d'anomalies corrélées.

Une panne se manifeste rarement seule : un PVC bloqué produit un pod
Pending, qui vide les endpoints du service, qui fait échouer le
déploiement. Sans regroupement, c'est quatre diagnostics au lieu d'un.
"""

import time

import pytest

from devops_agent.agent.correlation import Groupe, Tampon, _racine
from devops_agent.agent.watcher import Evenement


def ev(ressource="pod", nom="web", etat="Panne", ns="prod", gravite="grave"):
    return Evenement(pod=nom, namespace=ns, etat=etat,
                     gravite=gravite, ressource=ressource)


class TestNormalisationDesNoms:
    """Rapprocher un pod de son service suppose d'ôter les suffixes générés."""

    @pytest.mark.parametrize("nom,attendu", [
        ("web-7d9f8b4c5-x2k4p", "web"),        # ReplicaSet
        ("postgres-0", "postgres"),             # StatefulSet
        ("batch-job-a1b2c", "batch-job"),       # Job
        ("api", "api"),                         # déjà nu
    ])
    def test_suffixes_retires(self, nom, attendu):
        assert _racine(nom) == attendu


class TestGroupement:
    def test_cascade_regroupee(self):
        """Le cas qui motive tout le module."""
        t = Tampon(fenetre=0)
        for e in [
            ev("pvc", "postgres-data", "VolumeNonLie"),
            ev("pod", "postgres-0", "Pending"),
            ev("service", "postgres", "SansEndpoint"),
            ev("deployment", "postgres", "AucunReplicaPret", gravite="critique"),
        ]:
            t.ajouter(e)
        groupes = t.prets()
        assert len(groupes) == 1
        assert len(groupes[0].evenements) == 4

    def test_namespaces_differents_separes(self):
        t = Tampon(fenetre=0)
        t.ajouter(ev(nom="api", ns="prod"))
        t.ajouter(ev(nom="api", ns="staging"))
        assert len(t.prets()) == 2

    def test_charges_differentes_separees(self):
        t = Tampon(fenetre=0)
        t.ajouter(ev(nom="postgres-0"))
        t.ajouter(ev(nom="redis-0"))
        assert len(t.prets()) == 2

    def test_probleme_de_noeud_rejoint_tout(self):
        """Un nœud en difficulté explique potentiellement tout le reste."""
        t = Tampon(fenetre=0)
        t.ajouter(ev("node", "node-3", "NoeudNotReady", ns="-", gravite="critique"))
        t.ajouter(ev("pod", "api-x", "Pending", ns="prod"))
        t.ajouter(ev("pod", "web-y", "Pending", ns="staging"))
        assert len(t.prets()) == 1

    def test_gravite_du_groupe_est_la_plus_haute(self):
        g = Groupe(evenements=[
            ev(gravite="surveillance"), ev(gravite="critique"), ev(gravite="grave"),
        ])
        assert g.gravite == "critique"


class TestFenetre:
    def test_groupe_jeune_attend(self):
        t = Tampon(fenetre=10)
        t.ajouter(ev())
        assert t.prets() == []

    def test_groupe_mur_est_livre(self):
        t = Tampon(fenetre=10)
        t.ajouter(ev())
        t.groupes[0].premiere = time.time() - 11
        assert len(t.prets()) == 1

    def test_groupe_livre_nest_plus_en_attente(self):
        t = Tampon(fenetre=0)
        t.ajouter(ev())
        assert len(t.prets()) == 1
        assert t.prets() == []

    def test_groupe_sature_sort_immediatement(self):
        """Un cluster qui s'effondre ne doit pas retarder le diagnostic."""
        from devops_agent.agent.correlation import MAX_PAR_GROUPE
        t = Tampon(fenetre=999)
        for i in range(MAX_PAR_GROUPE):
            t.ajouter(ev(nom="postgres-0"))
        assert len(t.prets()) == 1

    def test_vider_rend_tout(self):
        t = Tampon(fenetre=999)
        t.ajouter(ev(nom="a-0"))
        t.ajouter(ev(nom="b-0"))
        assert len(t.vider()) == 2
        assert t.groupes == []


class TestQuestionGroupee:
    def test_anomalie_seule_garde_sa_question_dediee(self):
        from devops_agent.agent.autonome import _question_groupe
        g = Groupe(evenements=[ev("pod", "web", "OOMKilled")])
        assert "limite" in _question_groupe(g)

    def test_groupe_demande_la_cause_racine(self):
        from devops_agent.agent.autonome import _question_groupe
        g = Groupe(evenements=[
            ev("pvc", "data", "VolumeNonLie"),
            ev("pod", "data-0", "Pending"),
        ])
        question = _question_groupe(g)
        assert "CAUSE RACINE" in question
        assert "conséquences" in question
        assert "VolumeNonLie" in question and "Pending" in question
