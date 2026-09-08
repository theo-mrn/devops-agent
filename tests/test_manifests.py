"""Indexation de manifests Kubernetes.

Le chunking markdown ne convient pas au YAML : un manifest n'a pas de
titres mais des documents séparés par `---`, chacun décrivant une
ressource complète.
"""

from pathlib import Path

import pytest

from devops_agent.ingestion import manifests

MANIFEST = """# Base de données de l'application, volontairement mono-instance
apiVersion: v1
kind: Namespace
metadata:
  name: demo
---
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: demo-postgres
  namespace: demo
  creationTimestamp: "2026-01-01T00:00:00Z"
  uid: abc-123
spec:
  instances: 1
status:
  phase: Healthy
---
apiVersion: bitnami.com/v1alpha1
kind: SealedSecret
metadata:
  name: demo-creds
  namespace: demo
spec:
  encryptedData:
    password: AgBv7x...
"""


@pytest.fixture
def depot(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "db.yml").write_text(MANIFEST)
    return tmp_path


class TestDecoupage:
    def test_une_ressource_par_chunk(self, depot):
        chunks = manifests.collecter(depot)
        # SealedSecret exclu : son contenu est chiffré.
        assert len(chunks) == 2

    def test_entete_situe_la_ressource(self, depot):
        chunks = manifests.collecter(depot)
        cluster = next(c for c in chunks if "Cluster" in c["titre"])
        assert "[Cluster demo/demo-postgres" in cluster["texte"]
        assert "app/db.yml" in cluster["texte"]

    def test_chunk_se_suffit_a_lui_meme(self, depot):
        """Un `spec:` isolé ne dit ni de quoi il parle ni où il vit."""
        for chunk in manifests.collecter(depot):
            assert chunk["titre"]
            assert chunk["fichier"]
            assert chunk["texte"].startswith("[")

    def test_specification_conservee(self, depot):
        chunks = manifests.collecter(depot)
        cluster = next(c for c in chunks if "Cluster" in c["titre"])
        assert "instances: 1" in cluster["texte"]


class TestNettoyage:
    def test_metadonnees_inutiles_retirees(self, depot):
        """uid et creationTimestamp polluent l'embedding sans rien apporter."""
        chunks = manifests.collecter(depot)
        cluster = next(c for c in chunks if "Cluster" in c["titre"])
        assert "creationTimestamp" not in cluster["texte"]
        assert "abc-123" not in cluster["texte"]

    def test_statut_retire(self, depot):
        """Le statut d'un manifest Git est périmé : kubectl fait foi."""
        chunks = manifests.collecter(depot)
        cluster = next(c for c in chunks if "Cluster" in c["titre"])
        assert "Healthy" not in cluster["texte"]

    def test_secrets_scelles_ignores(self, depot):
        titres = [c["titre"] for c in manifests.collecter(depot)]
        assert not any("SealedSecret" in t for t in titres)


class TestCommentaires:
    def test_intention_conservee(self, depot):
        """Les commentaires portent le POURQUOI, absent de kubectl."""
        chunks = manifests.collecter(depot)
        assert any("mono-instance" in c["texte"] for c in chunks)


class TestRobustesse:
    def test_yaml_invalide_reste_indexable(self, tmp_path):
        (tmp_path / "casse.yml").write_text("ceci: n'est: pas: du yaml: [")
        chunks = manifests.collecter(tmp_path)
        assert len(chunks) == 1, "un fichier illisible doit rester cherchable"

    def test_depot_absent(self):
        assert manifests.collecter(Path("/inexistant")) == []

    def test_fichiers_non_yaml_ignores(self, tmp_path):
        (tmp_path / "notes.txt").write_text("du texte")
        (tmp_path / "script.sh").write_text("#!/bin/sh")
        assert manifests.collecter(tmp_path) == []


class TestDocumentation:
    def test_markdown_decoupe_par_section(self, tmp_path):
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "runbook.md").write_text(
            "# Titre\n\nIntro.\n\n## Première\n\nA.\n\n## Seconde\n\nB.\n"
        )
        chunks = manifests.collecter_documentation(tmp_path)
        titres = [c["titre"] for c in chunks]
        assert "Première" in titres and "Seconde" in titres

    def test_source_distingue_runbook_et_manifest(self, tmp_path):
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "r.md").write_text("# Titre\n\nContenu suffisant ici.\n")
        (tmp_path / "app.yml").write_text(
            "apiVersion: v1\nkind: Namespace\nmetadata:\n  name: x\n"
        )
        assert all(c["source"] == "runbook"
                   for c in manifests.collecter_documentation(tmp_path))
        assert all(c["source"] == "infra" for c in manifests.collecter(tmp_path))
