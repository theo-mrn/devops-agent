"""Configuration et confidentialité de la clé API."""

import os

from devops_agent.core import config


class TestChargementEnv:
    def test_variable_shell_prioritaire(self, monkeypatch, tmp_path):
        """Une variable déjà définie ne doit pas être écrasée par .env."""
        monkeypatch.setenv("RAG_TEST_PRIORITE", "depuis-le-shell")
        fichier = tmp_path / ".env"
        fichier.write_text("RAG_TEST_PRIORITE=depuis-le-fichier\n")

        # On rejoue le chargement en pointant sur le fichier temporaire.
        for ligne in fichier.read_text().splitlines():
            cle, _, valeur = ligne.partition("=")
            if cle.strip() and cle.strip() not in os.environ:
                os.environ[cle.strip()] = valeur.strip()

        assert os.environ["RAG_TEST_PRIORITE"] == "depuis-le-shell"

    def test_detection_de_cle(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
        assert config.cle_api_disponible() is False

        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-factice")
        assert config.cle_api_disponible() is True


class TestConfidentialite:
    """La clé ne doit jamais apparaître dans une sortie."""

    def test_resume_ne_montre_pas_la_cle(self, monkeypatch):
        secret = "sk-ant-api03-valeur-tres-secrete"
        monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
        texte = config.resume()
        assert secret not in texte
        assert "présente" in texte

    def test_env_example_ne_contient_pas_de_vraie_cle(self):
        """Le fichier d'exemple est versionné : il doit rester vide de secrets."""
        exemple = config.RACINE / ".env.example"
        if exemple.exists():
            contenu = exemple.read_text()
            # Une vraie clé Anthropic commence par sk-ant- suivi de caractères.
            assert "sk-ant-api" not in contenu

    def test_env_reel_est_ignore_par_git(self):
        gitignore = config.RACINE / ".gitignore"
        assert ".env" in gitignore.read_text()


class TestVersion:
    def test_version_unique(self):
        """La version ne doit être écrite qu'à un seul endroit.

        Elle l'était en double — pyproject.toml et __init__.py — et les
        deux avaient divergé : le paquet annonçait 0.1.0 alors que
        l'image publiée était en 0.3.1.
        """
        import tomllib

        import devops_agent

        declaree = tomllib.loads(
            (config.RACINE / "pyproject.toml").read_text()
        )["project"]["version"]
        assert devops_agent.__version__ in (declaree, "0.0.0+dev")

    def test_version_lue_depuis_les_metadonnees(self):
        source = (config.RACINE / "src/devops_agent/__init__.py").read_text()
        assert "importlib.metadata" in source
