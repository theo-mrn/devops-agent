"""Détection de pression mémoire à partir de metrics-server.

Les détecteurs d'objets voient qu'un pod a ÉTÉ tué ; les métriques
permettent de voir qu'il VA l'être. Mais une mesure instantanée est
bruyante : ces tests vérifient surtout qu'un pic transitoire ne
déclenche rien.
"""

import time

import pytest

from devops_agent.agent import metriques as m


class TestConversions:
    """metrics-server et les manifests n'emploient pas les mêmes unités."""

    @pytest.mark.parametrize("valeur,octets", [
        ("1024Ki", 1024 * 1024),
        ("2Gi", 2 * 1024**3),
        ("512Mi", 512 * 1024**2),
        ("1000", 1000),
    ])
    def test_memoire(self, valeur, octets):
        assert m._quantite_vers_octets(valeur) == octets

    @pytest.mark.parametrize("valeur,millicores", [
        ("12700236n", 12.700236),   # format metrics-server
        ("500m", 500),               # format manifest
        ("2", 2000),                 # cœurs entiers
    ])
    def test_cpu(self, valeur, millicores):
        assert m._quantite_vers_millicores(valeur) == pytest.approx(millicores)

    def test_valeur_illisible(self):
        assert m._quantite_vers_octets("beaucoup") == 0.0


class TestConfirmation:
    """Le cœur du module : ne pas payer pour un pic de dix secondes."""

    def _surveiller(self, monkeypatch, part_utilisee, confirmations=3):
        limite = 1024 * 1024**2
        monkeypatch.setattr(m, "consommation_pods", lambda: {
            "prod/web": {"memoire": limite * part_utilisee, "cpu": 100}
        })
        monkeypatch.setattr(m, "limites_pods", lambda: {
            "prod/web": {"memoire": limite, "cpu": 1000}
        })
        return m.SurveillanceRessources(confirmations=confirmations)

    def test_un_seul_releve_ne_declenche_pas(self, monkeypatch):
        s = self._surveiller(monkeypatch, 0.95)
        assert s.relever() == []

    def test_confirmations_successives_declenchent(self, monkeypatch):
        s = self._surveiller(monkeypatch, 0.95, confirmations=3)
        assert s.relever() == []
        assert s.relever() == []
        anomalies = s.relever()
        assert len(anomalies) == 1
        assert anomalies[0][2] == "MemoireProcheDeLaLimite"

    def test_pic_transitoire_ignore(self, monkeypatch):
        """Haut, puis bas, puis haut : la série est rompue."""
        limite = 1024 * 1024**2
        etat = {"part": 0.95}
        monkeypatch.setattr(m, "consommation_pods", lambda: {
            "prod/web": {"memoire": limite * etat["part"], "cpu": 100}
        })
        monkeypatch.setattr(m, "limites_pods", lambda: {
            "prod/web": {"memoire": limite, "cpu": 1000}
        })
        s = m.SurveillanceRessources(confirmations=3)

        s.relever()
        etat["part"] = 0.40      # redescend
        s.relever()
        etat["part"] = 0.95      # remonte
        assert s.relever() == [], "un pic transitoire a déclenché une alerte"

    def test_sous_le_seuil_jamais_signale(self, monkeypatch):
        s = self._surveiller(monkeypatch, 0.50)
        for _ in range(5):
            assert s.relever() == []

    def test_pod_sans_limite_ignore(self, monkeypatch):
        """Sans limite déclarée, aucun ratio n'est calculable."""
        monkeypatch.setattr(m, "consommation_pods", lambda: {
            "prod/web": {"memoire": 10 * 1024**3, "cpu": 100}
        })
        monkeypatch.setattr(m, "limites_pods", lambda: {
            "prod/web": {"memoire": 0, "cpu": 0}
        })
        s = m.SurveillanceRessources(confirmations=1)
        assert s.relever() == []

    def test_pas_de_repetition_avant_une_heure(self, monkeypatch):
        s = self._surveiller(monkeypatch, 0.95, confirmations=1)
        assert len(s.relever()) == 1
        assert s.relever() == [], "le même pod a été signalé deux fois"

    def test_detail_chiffre(self, monkeypatch):
        """Le message doit porter des valeurs, pas des généralités."""
        s = self._surveiller(monkeypatch, 0.90, confirmations=1)
        _, _, _, detail = s.relever()[0]
        assert "Mi" in detail and "%" in detail


class TestCadence:
    def test_premier_releve_immediat(self):
        assert m.SurveillanceRessources().doit_relever() is True

    def test_pas_de_releve_avant_l_intervalle(self, monkeypatch):
        s = m.SurveillanceRessources()
        s.dernier_releve = time.time()
        assert s.doit_relever() is False


class TestCoutNul:
    def test_aucun_appel_au_modele(self):
        import inspect
        source = inspect.getsource(m)
        assert "anthropic" not in source and "ollama" not in source
