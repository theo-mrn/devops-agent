"""Détecteurs par type de ressource.

Un pod sain ne garantit pas une application saine : ces règles couvrent
les pannes que la seule surveillance des pods laisse passer.
"""

from datetime import datetime, timedelta, timezone

import pytest

from devops_agent.agent import detecteurs


def _meta(nom="objet", ns="prod", age_minutes=10):
    date = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    return {"name": nom, "namespace": ns,
            "creationTimestamp": date.isoformat().replace("+00:00", "Z")}


class TestEndpoints:
    """La panne la plus sournoise : pods sains, service injoignable."""

    def test_sans_adresse_detecte(self):
        objet = {"metadata": _meta("api"), "subsets": []}
        verdict = detecteurs.endpoints(objet)
        assert verdict == ("SansEndpoint", "grave")

    def test_avec_adresses_sain(self):
        objet = {"metadata": _meta("api"),
                 "subsets": [{"addresses": [{"ip": "10.0.0.1"}]}]}
        assert detecteurs.endpoints(objet) is None

    def test_objet_recent_epargne(self):
        """Un objet qui vient d'être créé traverse des états transitoires."""
        objet = {"metadata": _meta("api", age_minutes=0), "subsets": []}
        assert detecteurs.endpoints(objet) is None

    def test_services_systeme_ignores(self):
        objet = {"metadata": _meta("kube-dns", ns="kube-system"), "subsets": []}
        assert detecteurs.endpoints(objet) is None


class TestDeployment:
    def test_aucun_replica_pret_est_critique(self):
        objet = {"metadata": _meta("web"), "spec": {"replicas": 3},
                 "status": {"readyReplicas": 0}}
        assert detecteurs.deployment(objet) == ("AucunReplicaPret", "critique")

    def test_replicas_partiels_en_surveillance(self):
        objet = {"metadata": _meta("web"), "spec": {"replicas": 3},
                 "status": {"readyReplicas": 2}}
        etat, gravite = detecteurs.deployment(objet)
        assert "2/3" in etat and gravite == "surveillance"

    def test_rollout_bloque_detecte(self):
        objet = {"metadata": _meta("web"), "spec": {"replicas": 3},
                 "status": {"readyReplicas": 1,
                            "conditions": [{"type": "Progressing",
                                            "status": "False"}]}}
        assert detecteurs.deployment(objet) == ("RolloutBloque", "grave")

    def test_arret_volontaire_ignore(self):
        """replicas=0 est un choix, pas une panne."""
        objet = {"metadata": _meta("web"), "spec": {"replicas": 0},
                 "status": {}}
        assert detecteurs.deployment(objet) is None

    def test_complet_sain(self):
        objet = {"metadata": _meta("web"), "spec": {"replicas": 3},
                 "status": {"readyReplicas": 3}}
        assert detecteurs.deployment(objet) is None


class TestPVC:
    def test_pending_detecte(self):
        objet = {"metadata": _meta("data"), "status": {"phase": "Pending"}}
        assert detecteurs.pvc(objet) == ("VolumeNonLie", "grave")

    def test_lost_est_critique(self):
        objet = {"metadata": _meta("data"), "status": {"phase": "Lost"}}
        assert detecteurs.pvc(objet) == ("VolumePerdu", "critique")

    def test_bound_sain(self):
        objet = {"metadata": _meta("data"), "status": {"phase": "Bound"}}
        assert detecteurs.pvc(objet) is None


class TestNode:
    def test_not_ready_critique(self):
        objet = {"metadata": _meta("node-1", ns=None),
                 "status": {"conditions": [{"type": "Ready", "status": "False"}]}}
        assert detecteurs.node(objet) == ("NoeudNotReady", "critique")

    @pytest.mark.parametrize("condition,attendu", [
        ("MemoryPressure", "PressionMemoire"),
        ("DiskPressure", "PressionDisque"),
        ("PIDPressure", "PressionPID"),
    ])
    def test_pressions_detectees(self, condition, attendu):
        objet = {"metadata": _meta("node-1"),
                 "status": {"conditions": [{"type": "Ready", "status": "True"},
                                           {"type": condition, "status": "True"}]}}
        assert detecteurs.node(objet)[0] == attendu

    def test_cordon_signale_sans_alarmer(self):
        objet = {"metadata": _meta("node-1"), "spec": {"unschedulable": True},
                 "status": {"conditions": [{"type": "Ready", "status": "True"}]}}
        assert detecteurs.node(objet) == ("NoeudNonPlanifiable", "surveillance")

    def test_noeud_sain(self):
        objet = {"metadata": _meta("node-1"),
                 "status": {"conditions": [{"type": "Ready", "status": "True"},
                                           {"type": "MemoryPressure", "status": "False"}]}}
        assert detecteurs.node(objet) is None


class TestJob:
    def test_echec_definitif(self):
        objet = {"metadata": _meta("batch"),
                 "status": {"conditions": [{"type": "Failed", "status": "True"}]}}
        assert detecteurs.job(objet) == ("JobEchoue", "grave")

    def test_reussite_sain(self):
        objet = {"metadata": _meta("batch"), "status": {"succeeded": 1}}
        assert detecteurs.job(objet) is None


class TestRegistre:
    def test_chaque_ressource_par_defaut_a_un_detecteur(self):
        for ressource in detecteurs.PAR_DEFAUT:
            assert ressource in detecteurs.DETECTEURS

    def test_tous_les_detecteurs_sont_appelables(self):
        for fonction, libelle in detecteurs.DETECTEURS.values():
            assert callable(fonction)
            assert isinstance(libelle, str)
