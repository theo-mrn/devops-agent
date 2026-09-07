"""Aperçu de cluster : condensation et détection d'anomalies."""

import pytest

from devops_agent.agent import overview


def _noeud(nom="node-1", pret=True, pressions=None):
    return {"nom": nom, "pret": pret, "version": "v1.29.4",
            "pressions": pressions or []}


def _pod(nom, ns="prod", etat="Running", redemarrages=0):
    return {"nom": nom, "namespace": ns, "etat": etat,
            "redemarrages": redemarrages,
            "sain": etat in overview.ETATS_SAINS and redemarrages < 5}


class TestFormatage:
    def test_cluster_sain(self):
        texte = overview._formater(
            [_noeud()], [_pod(f"web-{i}") for i in range(10)], "v1.29.4"
        )
        assert "✓" in texte
        assert "10 pods sont sains" in texte

    def test_noeud_non_pret_signale(self):
        texte = overview._formater(
            [_noeud("node-1"), _noeud("node-2", pret=False)], [], "v1.29.4"
        )
        assert "NotReady" in texte and "node-2" in texte

    def test_pression_signalee(self):
        texte = overview._formater(
            [_noeud("node-1", pressions=["Memory"])], [], "v1.29.4"
        )
        assert "pression" in texte and "Memory" in texte

    def test_anomalies_triees_par_gravite(self):
        pods = [
            _pod("peu", etat="CrashLoopBackOff", redemarrages=2),
            _pod("beaucoup", etat="CrashLoopBackOff", redemarrages=30),
        ]
        texte = overview._formater([_noeud()], pods, "v1.29.4")
        # Le plus dégradé doit apparaître en premier.
        assert texte.index("beaucoup") < texte.index("peu")

    def test_agregation_au_dela_du_seuil(self):
        pods = [
            _pod(f"c-{i}", etat="CrashLoopBackOff", redemarrages=10)
            for i in range(overview.MAX_ANOMALIES + 20)
        ]
        texte = overview._formater([_noeud()], pods, "v1.29.4")
        assert "et 20 autres" in texte
        assert "20× CrashLoopBackOff" in texte

    def test_reste_compact_sur_gros_cluster(self):
        """Le point de tout le module : ne pas saturer le contexte."""
        pods = (
            [_pod(f"ok-{i}") for i in range(500)]
            + [_pod(f"ko-{i}", etat="CrashLoopBackOff", redemarrages=5)
               for i in range(60)]
        )
        texte = overview._formater([_noeud(f"n-{i}") for i in range(20)],
                                   pods, "v1.29.4")
        # ~3,5 caractères par token : on vise nettement moins de 2000.
        assert len(texte) / 3.5 < 2000, f"{len(texte)/3.5:.0f} tokens, trop verbeux"


class TestEtatPod:
    def test_crashloop_detecte_malgre_phase_running(self):
        """Un pod en CrashLoopBackOff est en phase Running : la phase ment."""
        pod = {
            "status": {
                "phase": "Running",
                "containerStatuses": [{
                    "restartCount": 12,
                    "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                }],
            }
        }
        etat, redemarrages = overview._etat_pod(pod)
        assert etat == "CrashLoopBackOff"
        assert redemarrages == 12

    def test_oomkilled_dans_etat_precedent(self):
        pod = {
            "status": {
                "phase": "Running",
                "containerStatuses": [{
                    "restartCount": 3,
                    "state": {"running": {}},
                    "lastState": {"terminated": {"reason": "OOMKilled"}},
                }],
            }
        }
        etat, _ = overview._etat_pod(pod)
        assert "OOMKilled" in etat

    def test_pod_sain(self):
        pod = {"status": {"phase": "Running",
                          "containerStatuses": [{"restartCount": 0,
                                                 "state": {"running": {}}}]}}
        assert overview._etat_pod(pod) == ("Running", 0)


class TestSurete:
    def test_apercu_passe_par_les_gardes_fous(self, monkeypatch):
        """Le collecteur ne doit pas contourner la lecture seule."""
        appels = []
        monkeypatch.setattr(
            overview.tools, "verifier_commande",
            lambda c: appels.append(c) or "refusé pour le test",
        )
        assert overview._kubectl_json("get pods") is None
        assert appels, "la commande n'a pas été vérifiée"
