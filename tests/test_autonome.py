"""Mode autonome : construction des questions et contrôle du budget."""

import time

import pytest

from devops_agent.agent.autonome import Autonome, _question
from devops_agent.agent.watcher import Evenement


def evenement(etat="OOMKilled", pod="web-1", ns="prod", n=3):
    return Evenement(pod=pod, namespace=ns, etat=etat,
                     gravite="critique", redemarrages=n)


class TestQuestions:
    """La question doit être adaptée au symptôme, pas générique."""

    def test_oom_interroge_limite_ou_fuite(self):
        q = _question(evenement("OOMKilled"))
        assert "limite" in q
        assert "fuit" in q      # « l application fuit-elle ? »

    def test_crashloop_oriente_vers_les_logs(self):
        q = _question(evenement("CrashLoopBackOff"))
        assert "logs" in q and "sortie" in q

    def test_imagepull_liste_les_trois_causes(self):
        q = _question(evenement("ImagePullBackOff"))
        assert "tag" in q and "authentification" in q and "réseau" in q

    def test_pending_oriente_vers_la_planification(self):
        q = _question(evenement("Pending"))
        assert "planification" in q or "ressources" in q

    def test_etat_enrichi_reconnu(self):
        """overview produit « OOMKilled (précédent) » : le préfixe suffit."""
        q = _question(evenement("OOMKilled (précédent)"))
        assert "limite" in q

    def test_etat_inconnu_a_une_question_par_defaut(self):
        q = _question(evenement("EtatExotique"))
        assert "EtatExotique" in q and "Diagnostique" in q

    def test_identite_du_pod_toujours_presente(self):
        for etat in ("OOMKilled", "CrashLoopBackOff", "Pending", "Inconnu"):
            q = _question(evenement(etat, pod="payment", ns="prod"))
            assert "prod/payment" in q


class TestBudget:
    """Le plafond quotidien doit tenir : c'est ce qui rend l'outil sûr."""

    def test_budget_neuf_disponible(self):
        assert Autonome(budget_jour=5.0)._budget_disponible()

    def test_budget_epuise_bloque(self):
        a = Autonome(budget_jour=1.0)
        a.depenses = [(time.time(), 1.5)]
        assert not a._budget_disponible()

    def test_depenses_anciennes_ne_comptent_plus(self):
        """Fenêtre glissante de 24 h : hier ne pèse pas sur aujourd'hui."""
        a = Autonome(budget_jour=1.0)
        a.depenses = [(time.time() - 90000, 10.0)]   # il y a 25 h
        assert a._budget_disponible()

    def test_cumul_sur_la_fenetre(self):
        a = Autonome(budget_jour=1.0)
        a.depenses = [(time.time() - 3600, 0.4), (time.time(), 0.3)]
        assert a._depense_24h() == pytest.approx(0.7)
        assert a._budget_disponible()

    def test_budget_epuise_ne_lance_pas_de_diagnostic(self, capsys):
        a = Autonome(budget_jour=0.01)
        a.depenses = [(time.time(), 1.0)]
        a.traiter(evenement())      # ne doit pas appeler l'API
        sortie = capsys.readouterr().out
        assert "budget quotidien" in sortie
        assert a.diagnostics == 0


class TestSimulation:
    def test_dry_run_ne_depense_rien(self, capsys):
        a = Autonome(dry_run=True)
        a.traiter(evenement())
        assert "simulation" in capsys.readouterr().out
        assert a.diagnostics == 0
        assert a.depenses == []
