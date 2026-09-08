"""Masquage des secrets — le test le plus important pour la distribution.

Sans ce filtre, `kubectl logs` expédie des jetons de production vers une
API externe. Ces tests couvrent les formats réellement rencontrés.
"""

import pytest

from devops_agent.agent import sanitizer, tools


class TestFormatsReconnaissables:
    """Secrets identifiables à leur forme, masqués en entier."""

    @pytest.mark.parametrize("secret", [
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jV",
        "ghp_16C7e42F292c6912E7710c838347Ae178B4a",
        "glpat_AbCdEfGhIjKlMnOpQrSt",
        "AKIAIOSFODNN7EXAMPLE",
        "dckr_pat_AbCdEfGhIjKlMnOpQrStUv",
        "https://hooks.slack.com/services/T00/B00/XXXXXXXXXXXX",
    ])
    def test_masque_entierement(self, secret):
        resultat = sanitizer.masquer(f"valeur trouvée : {secret}")
        assert secret not in resultat
        assert sanitizer.MASQUE in resultat

    def test_cle_privee_pem(self):
        pem = ("-----BEGIN RSA PRIVATE KEY-----\n"
               "MIIEowIBAAKCAQEA1234567890\n"
               "-----END RSA PRIVATE KEY-----")
        resultat = sanitizer.masquer(pem)
        assert "MIIEowIBAAKCAQEA" not in resultat
        assert sanitizer.MASQUE in resultat


class TestEtiquettesPreservees:
    """Le modèle doit savoir QUOI a été masqué, sans voir la valeur."""

    @pytest.mark.parametrize("texte,etiquette", [
        ("DATABASE_PASSWORD=hunter2", "DATABASE_PASSWORD"),
        ("api_key: sk-proj-abcdefgh", "api_key"),
        ("x-auth-token: abc123def456", "x-auth-token"),
        ("AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI", "AWS_SECRET_ACCESS_KEY"),
    ])
    def test_nom_conserve_valeur_masquee(self, texte, etiquette):
        resultat = sanitizer.masquer(texte)
        assert etiquette in resultat
        assert sanitizer.MASQUE in resultat
        assert texte.split("=")[-1].split(":")[-1].strip() not in resultat

    def test_url_de_connexion(self):
        resultat = sanitizer.masquer("postgres://admin:s3cr3t@db.prod:5432/app")
        assert "s3cr3t" not in resultat and "admin" not in resultat
        assert "db.prod:5432" in resultat      # l'hôte reste utile au diagnostic

    def test_plusieurs_secrets_sur_une_ligne(self):
        resultat = sanitizer.masquer("token=ghp_abcdefghij1234567890 password=hunter2")
        assert sanitizer.compter_masques(resultat) == 2


class TestFauxPositifs:
    """Masquer une valeur utile nuit au diagnostic."""

    @pytest.mark.parametrize("texte", [
        "replicas=3",
        "memory: 512Mi",
        "image: nginx:1.21",
        "restartPolicy: Always",
        "exitCode: 137",
        "reason: OOMKilled",
        "token: null",
        "cpu=500m",
    ])
    def test_valeur_normale_preservee(self, texte):
        assert sanitizer.masquer(texte) == texte


class TestTroncature:
    def test_texte_court_intact(self):
        assert sanitizer.tronquer("abc", 100) == "abc"

    def test_debut_et_fin_conserves(self):
        """La fin d'un log porte souvent l'erreur fatale."""
        texte = "DEBUT" + "x" * 5000 + "ERREUR_FATALE"
        resultat = sanitizer.tronquer(texte, 1000)
        assert resultat.startswith("DEBUT")
        assert "ERREUR_FATALE" in resultat
        assert len(resultat) < len(texte)

    def test_secret_conserve_est_masque(self):
        """Un secret dans la partie gardée doit être masqué."""
        texte = "DB_PASSWORD=hunter2 " + "y" * 5000
        resultat = sanitizer.assainir(texte, 1000)
        assert "hunter2" not in resultat
        assert sanitizer.MASQUE in resultat

    def test_secret_tronque_disparait(self):
        """Un secret dans la zone supprimée n'atteint pas le modèle."""
        texte = "x" * 3000 + " DB_PASSWORD=hunter2 " + "y" * 3000
        resultat = sanitizer.assainir(texte, 500)
        assert "hunter2" not in resultat

    def test_performance_sur_gros_volume(self):
        """Un log volumineux ne doit pas bloquer l'agent."""
        import time
        debut = time.time()
        sanitizer.assainir("ligne de log ordinaire\n" * 20_000)
        assert time.time() - debut < 2.0


class TestPointDePassageUnique:
    """Aucune sortie d'outil ne doit atteindre l'API sans filtrage."""

    def test_executer_assainit(self):
        tools.IMPLEMENTATIONS["_faux"] = lambda: "DB_PASSWORD=hunter2"
        try:
            assert "hunter2" not in tools.executer("_faux", {})
        finally:
            del tools.IMPLEMENTATIONS["_faux"]

    def test_executer_tronque(self):
        tools.IMPLEMENTATIONS["_gros"] = lambda: "x" * 50_000
        try:
            assert len(tools.executer("_gros", {})) < 20_000
        finally:
            del tools.IMPLEMENTATIONS["_gros"]

    def test_apercu_assaini(self):
        """L'aperçu contourne executer() : il doit filtrer lui-même."""
        import inspect
        from devops_agent.agent import overview
        source = inspect.getsource(overview.apercu)
        assert "sanitizer" in source or "masquer" in source
