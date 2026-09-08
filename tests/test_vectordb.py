"""Index vectoriel en base.

Le fichier `corpus.pkl` ne convient pas à un déploiement industriel : il
porte le texte des manifests d'un dépôt privé, et le lire suppose
PyTorch dans le pod. Ces tests couvrent la logique qui s'en affranchit.
"""

import pytest

from devops_agent.retrieval import vectordb


class TestDisponibilite:
    def test_sans_dsn(self, monkeypatch):
        monkeypatch.setattr(vectordb, "DSN", "")
        assert vectordb.disponible() is False

    def test_avec_dsn_et_psycopg(self, monkeypatch):
        monkeypatch.setattr(vectordb, "DSN", "postgresql://x/y")
        pytest.importorskip("psycopg")
        assert vectordb.disponible() is True


class TestSchema:
    def test_dimension_coherente_avec_le_modele(self):
        """bge-m3 produit des vecteurs de 1024 dimensions.

        Une incohérence ici ferait échouer chaque insertion — autant la
        figer par un test.
        """
        assert vectordb.DIMENSION == 1024
        assert f"vector({vectordb.DIMENSION})" in vectordb.SCHEMA

    def test_extension_creee(self):
        assert "CREATE EXTENSION IF NOT EXISTS vector" in vectordb.SCHEMA

    def test_index_vectoriel_present(self):
        """Sans index, chaque recherche balaie toute la table."""
        assert "ivfflat" in vectordb.SCHEMA
        assert "vector_cosine_ops" in vectordb.SCHEMA

    def test_empreinte_unique(self):
        """Rend l'insertion idempotente : réindexer ne duplique pas."""
        assert "UNIQUE INDEX" in vectordb.SCHEMA
        assert "empreinte" in vectordb.SCHEMA


class TestEncodage:
    def test_service_http_prioritaire(self, monkeypatch):
        """C'est ce qui évite PyTorch dans le pod de l'agent."""
        import json
        from unittest.mock import MagicMock

        monkeypatch.setattr(vectordb, "EMBED_URL", "http://embed/vecteur")
        capture = {}

        def intercepter(requete, timeout=None):
            capture["url"] = requete.full_url
            reponse = MagicMock()
            reponse.read.return_value = json.dumps({"vecteur": [0.1] * 1024}).encode()
            reponse.__enter__ = lambda s: reponse
            reponse.__exit__ = lambda *a: None
            return reponse

        monkeypatch.setattr(vectordb.urllib.request, "urlopen", intercepter)
        vecteur = vectordb.encoder("une question")
        assert capture["url"] == "http://embed/vecteur"
        assert len(vecteur) == 1024

    def test_service_injoignable_ne_leve_pas(self, monkeypatch):
        monkeypatch.setattr(vectordb, "EMBED_URL", "http://injoignable.invalid/x")
        assert vectordb.encoder("question") is None


class TestOutilAgent:
    def test_base_prioritaire_sur_fichier(self, monkeypatch):
        """En cluster, la base doit l'emporter sur l'index local."""
        from devops_agent.agent import tools

        monkeypatch.setattr(vectordb, "disponible", lambda: True)
        monkeypatch.setattr(vectordb, "chercher", lambda q, k=3: [
            vectordb.Chunk("contenu trouvé", "Titre", "infra", "a.yml", 0.9)
        ])
        resultat = tools.chercher_documentation("question")
        assert "contenu trouvé" in resultat
        assert "infra/a.yml" in resultat

    def test_degradation_sans_index(self, monkeypatch):
        from devops_agent.agent import tools

        monkeypatch.setattr(vectordb, "disponible", lambda: False)
        monkeypatch.setattr(tools, "rag_disponible", lambda: False)
        resultat = tools.chercher_documentation("question")
        assert "indisponible" in resultat
        assert "autres outils" in resultat


class TestConfidentialite:
    def test_la_recherche_nutilise_pas_de_fichier(self):
        """La recherche doit passer par SQL, jamais par corpus.pkl.

        Un repli local subsiste dans `encoder()` pour l'usage hors
        cluster, mais `chercher()` ne doit dépendre que de la base —
        sinon l'index en base perdrait sa raison d'être.
        """
        import inspect
        source = inspect.getsource(vectordb.chercher)
        assert "pickle" not in source
        assert "INDEX" not in source

    def test_aucune_lecture_de_pickle(self):
        """Le module ne doit jamais désérialiser d'index sur disque.

        On inspecte le CODE, pas les commentaires : le docstring
        mentionne légitimement corpus.pkl pour expliquer pourquoi la
        base existe.
        """
        import ast
        import inspect

        arbre = ast.parse(inspect.getsource(vectordb))
        importes = {
            alias.name
            for noeud in ast.walk(arbre)
            if isinstance(noeud, ast.Import)
            for alias in noeud.names
        }
        assert "pickle" not in importes
