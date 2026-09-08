"""Manifests de déploiement et détection de l'environnement.

Le RBAC est la seconde couche de sûreté : le code refuse les écritures,
le serveur d'API les rend impossibles. Ces tests vérifient que le
manifest reste conforme à cette intention.
"""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

RACINE = Path(__file__).resolve().parents[1]
RBAC = RACINE / "deploy/rbac.yaml"


def documents(chemin: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(chemin.read_text()) if d]


@pytest.fixture(scope="module")
def cluster_role() -> dict:
    for doc in documents(RBAC):
        if doc.get("kind") == "ClusterRole":
            return doc
    pytest.fail("aucun ClusterRole dans deploy/rbac.yaml")


class TestRBACLectureSeule:
    """Aucun verbe d'écriture ne doit figurer dans le ClusterRole."""

    INTERDITS = {"create", "update", "patch", "delete", "deletecollection",
                 "*", "impersonate", "escalate", "bind"}

    def test_aucun_verbe_ecriture(self, cluster_role):
        for regle in cluster_role["rules"]:
            interdits = set(regle["verbs"]) & self.INTERDITS
            assert not interdits, (
                f"verbe d'écriture « {interdits} » sur {regle['resources']}"
            )

    def test_aucun_acces_aux_secrets(self, cluster_role):
        """Le contenu d'un Secret est encodé en base64, donc lisible."""
        for regle in cluster_role["rules"]:
            assert "secrets" not in regle["resources"]

    def test_aucune_execution_dans_un_conteneur(self, cluster_role):
        """pods/exec permettrait d'agir sur le cluster malgré le reste."""
        for regle in cluster_role["rules"]:
            for ressource in regle["resources"]:
                assert not ressource.endswith(("/exec", "/portforward",
                                               "/attach", "/scale"))

    def test_aucun_joker_sur_les_ressources(self, cluster_role):
        for regle in cluster_role["rules"]:
            assert "*" not in regle["resources"]


class TestRBACCouverture:
    """Le RBAC doit couvrir ce que l'agent surveille réellement."""

    @pytest.mark.parametrize("ressource", [
        "pods", "pods/log", "events", "services", "endpoints",
        "nodes", "persistentvolumeclaims",
        "deployments", "statefulsets", "jobs",
    ])
    def test_ressource_surveillee_accessible(self, cluster_role, ressource):
        toutes = {r for regle in cluster_role["rules"] for r in regle["resources"]}
        assert ressource in toutes

    def test_watch_autorise_sur_les_ressources_surveillees(self, cluster_role):
        """Sans `watch`, la surveillance par événements est impossible."""
        for regle in cluster_role["rules"]:
            if "pods" in regle["resources"] and "metrics" not in str(regle):
                assert "watch" in regle["verbs"]
                return
        pytest.fail("aucune règle n'autorise watch sur les pods")

    def test_detecteurs_et_rbac_concordent(self, cluster_role):
        """Chaque détecteur doit avoir la permission correspondante.

        kubectl accepte des abréviations (`pvc`) que le RBAC ne connaît
        pas : il exige le nom complet de la ressource.
        """
        from devops_agent.agent import detecteurs

        ALIAS = {
            "pvc": "persistentvolumeclaims",
            "pv": "persistentvolumes",
            "svc": "services",
            "deploy": "deployments",
            "sts": "statefulsets",
            "ds": "daemonsets",
            "cm": "configmaps",
            "ns": "namespaces",
            "no": "nodes",
            "po": "pods",
            "ep": "endpoints",
        }

        toutes = {r for regle in cluster_role["rules"] for r in regle["resources"]}
        for ressource in detecteurs.PAR_DEFAUT:
            nom = ALIAS.get(ressource, ressource)
            assert nom in toutes, (
                f"« {ressource} » est surveillé mais « {nom} » est absent du RBAC"
            )


class TestDetectionEnvironnement:
    def test_hors_cluster_par_defaut(self):
        from devops_agent.agent import tools
        # En test, le jeton de ServiceAccount n'existe pas.
        assert tools.dans_le_cluster() is False

    def test_contexte_decrit_le_mode(self):
        from devops_agent.agent import tools
        assert "kubeconfig" in tools.contexte_kubernetes()

    def test_in_cluster_detecte(self, tmp_path, monkeypatch):
        from devops_agent.agent import tools
        jeton = tmp_path / "token"
        jeton.write_text("factice")
        monkeypatch.setattr(tools, "JETON_IN_CLUSTER", jeton)
        assert tools.dans_le_cluster() is True


class TestManifestDeploiement:
    def test_une_seule_replique(self):
        """Deux agents diagnostiqueraient deux fois le même incident."""
        for doc in documents(RACINE / "deploy/agent.yaml"):
            if doc.get("kind") == "Deployment":
                assert doc["spec"]["replicas"] == 1
                return
        pytest.fail("aucun Deployment")

    def test_conteneur_non_privilegie(self):
        for doc in documents(RACINE / "deploy/agent.yaml"):
            if doc.get("kind") != "Deployment":
                continue
            spec = doc["spec"]["template"]["spec"]
            assert spec["securityContext"]["runAsNonRoot"] is True
            conteneur = spec["containers"][0]["securityContext"]
            assert conteneur["allowPrivilegeEscalation"] is False
            assert conteneur["readOnlyRootFilesystem"] is True
            assert conteneur["capabilities"]["drop"] == ["ALL"]
            return
        pytest.fail("aucun Deployment")

    def test_utilise_le_serviceaccount_dedie(self):
        for doc in documents(RACINE / "deploy/agent.yaml"):
            if doc.get("kind") == "Deployment":
                spec = doc["spec"]["template"]["spec"]
                assert spec["serviceAccountName"] == "devops-agent"
                return

    def test_budgets_plafonnes(self):
        """Un agent déployé sans plafond peut coûter cher."""
        for doc in documents(RACINE / "deploy/agent.yaml"):
            if doc.get("kind") == "ConfigMap":
                assert "RAG_BUDGET_MAX" in doc["data"]
                assert "RAG_BUDGET_JOUR" in doc["data"]
                return
        pytest.fail("aucune ConfigMap")
