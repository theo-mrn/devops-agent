"""Garde-fous de sûreté — les tests les plus importants du projet.

Un agent qui accède à un cluster de production ne doit pouvoir RIEN
modifier. Ces tests vérifient que les refus tiennent ; ils doivent être
exécutés avant toute distribution.
"""

import pytest

from devops_agent.agent import tools


class TestKubectlLectureSeule:
    """Aucun verbe d'écriture ne doit passer."""

    @pytest.mark.parametrize("commande", [
        "delete pod payment-svc -n prod",
        "apply -f manifest.yaml",
        "create deployment nginx --image=nginx",
        "patch deployment web -p '{}'",
        "scale deployment web --replicas=0",
        "drain node-1",
        "cordon node-1",
        "edit deployment web",
        "replace -f manifest.yaml",
        "rollout undo deployment/web",
        "exec -it pod -- sh",
        "port-forward pod 8080:80",
    ])
    def test_verbe_ecriture_refuse(self, commande):
        assert tools.verifier_commande(commande) is not None

    @pytest.mark.parametrize("commande", [
        "get pods -n prod",
        "describe pod web -n prod",
        "logs web -n prod --previous",
        "top pod -n prod",
        "events -n prod",
        "version",
    ])
    def test_verbe_lecture_autorise(self, commande):
        # Vérifie le garde-fou, pas le cluster : aucun appel réseau.
        assert tools.verifier_commande(commande) is None


class TestSecrets:
    """Le contenu des Secrets ne doit jamais sortir, même en lecture."""

    @pytest.mark.parametrize("commande", [
        "get secret db-credentials -n prod",
        "get secrets -n prod",
        "describe secret api-key -n prod",
        "get secret -o yaml -n prod",
    ])
    def test_secret_refuse(self, commande):
        motif = tools.verifier_commande(commande)
        assert motif is not None
        assert "base64" in motif


class TestFlagsDangereux:
    """Un flag d'écriture glissé dans une commande de lecture."""

    @pytest.mark.parametrize("commande", [
        "get pods --force",
        "get pods --filename manifest.yaml",
        "describe pod x --patch '{}'",
    ])
    def test_flag_refuse(self, commande):
        assert tools.verifier_commande(commande) is not None


class TestTraversee:
    """La lecture de fichiers ne doit pas sortir du dépôt configuré."""

    @pytest.mark.parametrize("chemin", [
        "../../../etc/passwd",
        "../../.ssh/id_rsa",
        "/etc/shadow",
        "../../../../root/.bashrc",
    ])
    def test_traversee_refusee(self, chemin):
        resultat = tools.lire_fichier(chemin)
        assert resultat.startswith(("[refusé]", "[erreur]"))


class TestNamespaces:
    """La restriction de namespaces, quand elle est configurée."""

    def test_namespace_hors_liste_refuse(self, monkeypatch):
        monkeypatch.setattr(tools, "NAMESPACES_AUTORISES", {"prod", "staging"})
        assert tools.verifier_commande("get pods -n interdit") is not None

    def test_namespace_autorise_passe(self, monkeypatch):
        monkeypatch.setattr(tools, "NAMESPACES_AUTORISES", {"prod", "staging"})
        assert tools.verifier_commande("get pods -n prod") is None

    def test_all_namespaces_refuse_si_liste(self, monkeypatch):
        monkeypatch.setattr(tools, "NAMESPACES_AUTORISES", {"prod"})
        assert tools.verifier_commande("get pods --all-namespaces") is not None


class TestDispatch:
    """Le routage des appels d'outils."""

    def test_outil_inconnu(self):
        assert tools.executer("rm_rf", {}).startswith("[erreur]")

    def test_arguments_invalides(self):
        assert tools.executer("kubectl", {"mauvais": "arg"}).startswith("[erreur]")

    def test_tous_les_outils_declares_sont_implementes(self):
        declares = {d["name"] for d in tools.DEFINITIONS}
        assert declares == set(tools.IMPLEMENTATIONS)
