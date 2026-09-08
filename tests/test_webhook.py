"""Envoi des rapports vers un webhook.

Un rapport qui reste dans les logs d'un pod n'est lu par personne. Mais
l'envoi ne doit jamais compromettre la surveillance ni fuiter de secrets.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from devops_agent.agent import sanitizer, webhook
from devops_agent.agent.correlation import Groupe
from devops_agent.agent.watcher import Evenement


def groupe(gravite="grave", ns="prod"):
    return Groupe(evenements=[
        Evenement(pod="web-1", namespace=ns, etat="CrashLoopBackOff",
                  gravite=gravite, redemarrages=5, ressource="pod")
    ])


class TestCharge:
    def test_contient_l_essentiel(self):
        charge = webhook.construire_charge(
            groupe(), "## Diagnostic\nOOM.", 0.03, ["kubectl"], []
        )
        assert charge["namespace"] == "prod"
        assert charge["gravite"] == "grave"
        assert charge["cout_dollars"] == 0.03
        assert len(charge["anomalies"]) == 1
        assert charge["anomalies"][0]["etat"] == "CrashLoopBackOff"

    def test_rapport_assaini(self):
        """Un rapport cite des logs : il peut contenir des identifiants."""
        rapport = "La variable DB_PASSWORD=hunter2 est en cause"
        charge = webhook.construire_charge(groupe(), rapport, 0.0, [], [])
        assert "hunter2" not in charge["diagnostic"]
        assert sanitizer.MASQUE in charge["diagnostic"]

    def test_verifications_assainies(self):
        charge = webhook.construire_charge(
            groupe(), "ok", 0.0, [], ["token=ghp_abcdefghij1234567890"]
        )
        assert "ghp_abcdefghij" not in charge["verifications"][0]

    def test_serialisable_en_json(self):
        charge = webhook.construire_charge(groupe(), "ok", 0.01, ["kubectl"], [])
        json.dumps(charge)      # ne doit pas lever


class TestFiltrageParGravite:
    @pytest.mark.parametrize("minimum,gravite,attendu", [
        ("surveillance", "surveillance", True),
        ("surveillance", "critique", True),
        ("critique", "surveillance", False),
        ("critique", "critique", True),
        ("grave", "surveillance", False),
        ("grave", "grave", True),
    ])
    def test_seuil(self, monkeypatch, minimum, gravite, attendu):
        monkeypatch.setattr(webhook, "GRAVITE_MIN", minimum)
        monkeypatch.setattr(webhook, "URL", "https://exemple/hook")
        with patch.object(webhook, "envoyer", return_value=True) as envoi:
            webhook.notifier(groupe(gravite), "rapport", 0.0, [])
        assert envoi.called is attendu


class TestResilience:
    """Un webhook indisponible ne doit jamais arrêter la surveillance."""

    def test_sans_url_ne_fait_rien(self, monkeypatch):
        monkeypatch.setattr(webhook, "URL", "")
        assert webhook.notifier(groupe(), "rapport", 0.0, []) is False

    def test_erreur_reseau_ne_leve_pas(self, monkeypatch):
        monkeypatch.setattr(webhook, "URL", "https://injoignable.invalid/hook")
        monkeypatch.setattr(webhook, "TENTATIVES", 1)
        # Ne doit pas lever, seulement renvoyer False.
        assert webhook.envoyer({"test": True}) is False

    def test_erreur_client_nest_pas_reessayee(self, monkeypatch):
        """Une 404 ne se corrigera pas en réessayant."""
        import urllib.error

        monkeypatch.setattr(webhook, "URL", "https://exemple/hook")
        appels = []

        def refuser(*a, **k):
            appels.append(1)
            raise urllib.error.HTTPError("u", 404, "Not Found", {}, None)

        monkeypatch.setattr(webhook.urllib.request, "urlopen", refuser)
        assert webhook.envoyer({"test": True}) is False
        assert len(appels) == 1, "une 4xx a été réessayée"

    def test_erreur_serveur_est_reessayee(self, monkeypatch):
        import urllib.error

        monkeypatch.setattr(webhook, "URL", "https://exemple/hook")
        monkeypatch.setattr(webhook, "TENTATIVES", 2)
        monkeypatch.setattr(webhook.time, "sleep", lambda s: None)
        appels = []

        def echouer(*a, **k):
            appels.append(1)
            raise urllib.error.HTTPError("u", 503, "Unavailable", {}, None)

        monkeypatch.setattr(webhook.urllib.request, "urlopen", echouer)
        webhook.envoyer({"test": True})
        assert len(appels) == 2


class TestAuthentification:
    def test_entete_ajoute_si_configure(self, monkeypatch):
        monkeypatch.setattr(webhook, "URL", "https://exemple/hook")
        monkeypatch.setattr(webhook, "AUTH", "Bearer jeton-test")
        capture = {}

        def intercepter(requete, timeout=None):
            capture["entetes"] = requete.headers
            reponse = MagicMock()
            reponse.status = 200
            reponse.__enter__ = lambda s: reponse
            reponse.__exit__ = lambda *a: None
            return reponse

        monkeypatch.setattr(webhook.urllib.request, "urlopen", intercepter)
        webhook.envoyer({"test": True})
        assert capture["entetes"].get("Authorization") == "Bearer jeton-test"


class TestDestinationsParGravite:
    """Un canal qui bipe l'équipe n'a pas à recevoir le bruit de fond."""

    def test_destination_dediee_prioritaire(self, monkeypatch):
        monkeypatch.setattr(webhook, "URL", "https://defaut/hook")
        monkeypatch.setattr(webhook, "URLS_PAR_GRAVITE", {
            "critique": "https://urgences/hook", "grave": "", "surveillance": ""
        })
        assert webhook.destination("critique") == "https://urgences/hook"

    def test_repli_sur_la_destination_generique(self, monkeypatch):
        monkeypatch.setattr(webhook, "URL", "https://defaut/hook")
        monkeypatch.setattr(webhook, "URLS_PAR_GRAVITE", {
            "critique": "https://urgences/hook", "grave": "", "surveillance": ""
        })
        assert webhook.destination("grave") == "https://defaut/hook"

    def test_aucune_destination(self, monkeypatch):
        monkeypatch.setattr(webhook, "URL", "")
        monkeypatch.setattr(webhook, "URLS_PAR_GRAVITE",
                            {"critique": "", "grave": "", "surveillance": ""})
        assert webhook.destination("critique") == ""
        assert webhook.actif() is False

    def test_actif_avec_une_seule_destination_dediee(self, monkeypatch):
        monkeypatch.setattr(webhook, "URL", "")
        monkeypatch.setattr(webhook, "URLS_PAR_GRAVITE", {
            "critique": "https://urgences/hook", "grave": "", "surveillance": ""
        })
        assert webhook.actif() is True

    def test_destination_dediee_ignore_le_seuil(self, monkeypatch):
        """Configurer une URL pour « surveillance » exprime déjà l'intention
        de recevoir ces événements : le seuil global ne doit pas la filtrer."""
        monkeypatch.setattr(webhook, "URL", "")
        monkeypatch.setattr(webhook, "GRAVITE_MIN", "critique")
        monkeypatch.setattr(webhook, "URLS_PAR_GRAVITE", {
            "critique": "", "grave": "", "surveillance": "https://journal/hook"
        })
        with patch.object(webhook, "envoyer", return_value=True) as envoi:
            webhook.notifier(groupe("surveillance"), "rapport", 0.0, [])
        assert envoi.called

    def test_seuil_applique_a_la_destination_generique(self, monkeypatch):
        monkeypatch.setattr(webhook, "URL", "https://defaut/hook")
        monkeypatch.setattr(webhook, "GRAVITE_MIN", "critique")
        monkeypatch.setattr(webhook, "URLS_PAR_GRAVITE",
                            {"critique": "", "grave": "", "surveillance": ""})
        with patch.object(webhook, "envoyer", return_value=True) as envoi:
            webhook.notifier(groupe("surveillance"), "rapport", 0.0, [])
        assert not envoi.called

    def test_envoi_vers_la_bonne_url(self, monkeypatch):
        monkeypatch.setattr(webhook, "URL", "https://defaut/hook")
        monkeypatch.setattr(webhook, "URLS_PAR_GRAVITE", {
            "critique": "https://urgences/hook", "grave": "", "surveillance": ""
        })
        capture = {}

        def intercepter(requete, timeout=None):
            capture["url"] = requete.full_url
            reponse = MagicMock()
            reponse.status = 200
            reponse.__enter__ = lambda s: reponse
            reponse.__exit__ = lambda *a: None
            return reponse

        monkeypatch.setattr(webhook.urllib.request, "urlopen", intercepter)
        webhook.envoyer({"gravite": "critique", "test": True})
        assert capture["url"] == "https://urgences/hook"
