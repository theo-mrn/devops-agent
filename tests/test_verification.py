"""Vérification mécanique des affirmations d'un diagnostic.

Un modèle peut affirmer « limite mémoire : 512Mi » alors qu'elle est à
2Gi. Cette erreur factuelle est le risque principal d'un agent, et elle
se contrôle sans aucun appel au modèle.
"""

import pytest

from devops_agent.agent import verification as v


class TestNormalisationDesQuantites:
    """Un modèle peut employer une autre unité sans se tromper."""

    @pytest.mark.parametrize("a,b", [
        ("2Gi", "2048Mi"),
        ("1Gi", "1024Mi"),
        ("512Mi", "512Mi"),
        ("500m", "0.5"),
        ("1000m", "1"),
    ])
    def test_equivalences_reconnues(self, a, b):
        assert v._equivalentes(a, b)

    @pytest.mark.parametrize("a,b", [
        ("1Gi", "512Mi"),
        ("100Mi", "1Gi"),
        ("500m", "1"),
    ])
    def test_differences_detectees(self, a, b):
        assert not v._equivalentes(a, b)

    def test_valeur_illisible(self):
        assert v._normaliser_quantite("beaucoup") is None


class TestExtraction:
    """Les affirmations doivent être trouvées dans un rapport rédigé."""

    @pytest.mark.parametrize("texte,attendu", [
        ("La limite mémoire est à 512Mi", "512Mi"),
        ("`resources.limits.memory`: 2Gi", "2Gi"),
        ("- limite de mémoire : 1Gi", "1Gi"),
        ("memory: 256Mi", "256Mi"),
    ])
    def test_memoire_extraite(self, texte, attendu):
        assert v._premiere_capture(v.MOTIFS_MEMOIRE, texte) == attendu

    def test_rien_a_extraire(self):
        assert v._premiere_capture(v.MOTIFS_MEMOIRE, "le pod a redémarré") is None

    @pytest.mark.parametrize("texte,attendu", [
        ("3 replicas configurés", "3"),
        ("replicas: 5", "5"),
    ])
    def test_replicas_extraits(self, texte, attendu):
        assert v._premiere_capture(v.MOTIFS_REPLICAS, texte) == attendu


class TestVerdicts:
    """La comparaison rapport / cluster, sans toucher au cluster."""

    def test_valeur_juste_confirmee(self, monkeypatch):
        monkeypatch.setattr(v, "_valeur_cluster", lambda *a, **k: "2Gi")
        resultats = v.verifier("limite mémoire : 2Gi", "pod", "p", "ns")
        assert resultats[0].verdict == "confirme"

    def test_valeur_fausse_contredite(self, monkeypatch):
        monkeypatch.setattr(v, "_valeur_cluster", lambda *a, **k: "2Gi")
        resultats = v.verifier("limite mémoire : 512Mi", "pod", "p", "ns")
        assert resultats[0].verdict == "contredit"
        assert resultats[0].reelle == "2Gi"

    def test_unite_differente_reste_confirmee(self, monkeypatch):
        """2048Mi et 2Gi désignent la même chose."""
        monkeypatch.setattr(v, "_valeur_cluster", lambda *a, **k: "2Gi")
        resultats = v.verifier("limite mémoire : 2048Mi", "pod", "p", "ns")
        assert resultats[0].verdict == "confirme"

    def test_cluster_muet_reste_inverifiable(self, monkeypatch):
        monkeypatch.setattr(v, "_valeur_cluster", lambda *a, **k: None)
        resultats = v.verifier("limite mémoire : 512Mi", "pod", "p", "ns")
        assert resultats[0].verdict == "invérifiable"

    def test_aucune_affirmation_aucun_controle(self):
        assert v.verifier("le pod a redémarré", "pod", "p", "ns") == []

    def test_ressource_non_couverte(self):
        """Mieux vaut ne rien dire que contredire à tort."""
        assert v.verifier("limite mémoire : 512Mi", "node", "n", "-") == []


class TestFormatage:
    def test_rapport_vide_sans_controle(self):
        assert v.formater([]) == ""

    def test_contradiction_declenche_un_avertissement(self):
        a = v.Affirmation("limite mémoire", "512Mi", "2Gi", "contredit")
        rendu = v.formater([a])
        assert "⚠" in rendu and "contredite" in rendu

    def test_confirmation_sans_avertissement(self):
        a = v.Affirmation("limite mémoire", "2Gi", "2Gi", "confirme")
        assert "⚠" not in v.formater([a])

    def test_constat_nest_pas_une_contradiction(self):
        """Un fait établi par le contrôle n'accuse pas le rapport."""
        a = v.Affirmation("sélecteur", "aucun pod", "0", "constat")
        rendu = v.formater([a])
        assert "!" in rendu and "⚠" not in rendu


class TestCoutNul:
    def test_aucun_appel_au_modele(self):
        """Le module ne doit jamais importer de client LLM."""
        import inspect
        source = inspect.getsource(v)
        assert "anthropic" not in source
        assert "ollama" not in source
