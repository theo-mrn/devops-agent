"""Expansion de requête et garde-fou de domaine."""

import pytest

from devops_agent.retrieval import expansion


class TestGlossaire:
    @pytest.mark.parametrize("question,attendu", [
        ("Comment planifier une tâche récurrente ?", "CronJob"),
        ("Comment partager des fichiers entre jobs ?", "artifacts"),
        ("Le disque est plein à cause de Docker", "prune"),
        ("Un pod redémarre en boucle", "CrashLoopBackOff"),
    ])
    def test_terme_technique_ajoute(self, question, attendu):
        assert attendu in expansion.termes_ajoutes(question)

    def test_terme_deja_present_non_duplique(self):
        # La question emploie déjà le vocabulaire du corpus.
        assert expansion.termes_ajoutes("Quelle est la syntaxe du bloc moved ?") == []

    def test_formulation_conservee(self):
        q = "Comment planifier une tâche récurrente ?"
        assert q in expansion.etendre(q)


class TestGardeFouDomaine:
    @pytest.mark.parametrize("question", [
        "Comment installer PostgreSQL sur Debian ?",
        "Comment écrire un playbook Ansible ?",
        "Comment créer un chart Helm ?",
        "Comment configurer un pipeline Jenkins ?",
    ])
    def test_hors_domaine_detecte(self, question):
        assert expansion.hors_domaine(question) is not None

    @pytest.mark.parametrize("question", [
        "Un pod est en CrashLoopBackOff, que faire ?",
        "Quelle est la syntaxe du bloc moved en Terraform ?",
        "Comment limiter la mémoire d'un conteneur Docker ?",
        "Comment voir les ports en écoute sur Linux ?",
    ])
    def test_domaine_couvert_passe(self, question):
        assert expansion.hors_domaine(question) is None

    def test_techno_hors_domaine_mais_sujet_couvert(self):
        # « nginx dans un pod Kubernetes » relève bien du corpus.
        assert expansion.hors_domaine("Comment déployer nginx sur Kubernetes ?") is None
