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
    """Aucun verbe d'écriture ne doit figurer dans le ClusterRole.

    Le rôle autorise la lecture de TOUTE ressource — c'est ce qui rend
    l'agent installable sans éditer le RBAC chez chaque client. Toute la
    sûreté repose donc sur les verbes : ces tests sont la garantie.
    """

    INTERDITS = {"create", "update", "patch", "delete", "deletecollection",
                 "*", "impersonate", "escalate", "bind"}

    def test_aucun_verbe_ecriture(self, cluster_role):
        for regle in cluster_role["rules"]:
            interdits = set(regle["verbs"]) & self.INTERDITS
            assert not interdits, (
                f"verbe d'écriture « {interdits} » sur {regle['resources']}"
            )

    def test_seuls_des_verbes_de_lecture(self, cluster_role):
        """Le joker sur les ressources n'est acceptable qu'avec ces verbes."""
        autorises = {"get", "list", "watch"}
        for regle in cluster_role["rules"]:
            assert set(regle["verbs"]) <= autorises, (
                f"verbe hors lecture sur {regle.get('apiGroups')}"
            )

    def test_execution_inaccessible(self, cluster_role):
        """pods/exec et pods/portforward exigent `create`, absent du rôle."""
        verbes = {v for regle in cluster_role["rules"] for v in regle["verbs"]}
        assert "create" not in verbes

    def test_secrets_bloques_par_le_code(self):
        """Le RBAC les autorise ; le code doit les refuser.

        C'est le contrat qui rend le joker acceptable : Kubernetes ne sait
        pas exclure une ressource d'un joker, donc le filtrage vit dans
        `tools.RESSOURCES_INTERDITES`.
        """
        from devops_agent.agent import tools

        for commande in ("get secret db -n prod", "get secrets -A",
                         "describe secret x -n prod"):
            assert tools.verifier_commande(commande) is not None, commande

    def test_ressources_sensibles_bloquees(self):
        """Le joker expose aussi les CRD portant des identifiants."""
        from devops_agent.agent import tools

        for commande in ("get certificaterequests -A",
                         "get secretstores -A",
                         "get clustersecretstores -A"):
            assert tools.verifier_commande(commande) is not None, commande

    def test_ressources_chiffrees_lisibles(self):
        """Un SealedSecret est chiffré : le lire ne révèle rien."""
        from devops_agent.agent import tools

        assert tools.verifier_commande("get sealedsecret x -n prod") is None


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


class TestRAGOptionnel:
    """L'agent doit fonctionner sans les dépendances de recherche.

    Elles pèsent ~2 Go et ne servent qu'à un outil sur cinq. Un
    déploiement qui n'a pas de documentation interne à indexer doit
    pouvoir s'en passer — et le jour où il en a, `uv sync --extra rag`
    suffit.
    """

    def test_message_explicite_sans_dependances(self, monkeypatch):
        from devops_agent.agent import tools
        monkeypatch.setattr(tools, "rag_disponible", lambda: False)
        resultat = tools.chercher_documentation("une question")
        assert "indisponible" in resultat
        assert "--extra rag" in resultat
        # Le modèle doit savoir qu'il peut continuer autrement.
        assert "autres outils" in resultat

    def test_les_autres_outils_ne_dependent_pas_du_rag(self):
        """Quatre outils sur cinq doivent rester utilisables."""
        import inspect
        from devops_agent.agent import tools

        for nom in ("kubectl", "lire_fichier", "lister_fichiers",
                    "ressources_pod", "rafraichir_apercu"):
            source = inspect.getsource(getattr(tools, nom))
            assert "sentence_transformers" not in source
            assert "retrieval" not in source

    def test_dependances_rag_bien_optionnelles(self):
        """pyproject doit les déclarer en extra, pas en dépendance de base."""
        import tomllib
        config = tomllib.loads((RACINE / "pyproject.toml").read_text())
        base = " ".join(config["project"]["dependencies"])
        extra = " ".join(config["project"]["optional-dependencies"]["rag"])

        assert "sentence-transformers" not in base
        assert "sentence-transformers" in extra
        # anthropic reste indispensable : c'est le moteur de l'agent.
        assert "anthropic" in base




STRICT = RACINE / "deploy/rbac-strict.yaml"


@pytest.fixture(scope="module")
def cluster_role_strict() -> dict:
    for doc in documents(STRICT):
        if doc.get("kind") == "ClusterRole":
            return doc
    pytest.fail("aucun ClusterRole dans deploy/rbac-strict.yaml")


class TestRBACStrict:
    """La variante qui bloque les Secrets au niveau du serveur d'API.

    Le RBAC n'a aucun mécanisme de refus — il est purement additif. La
    seule façon d'interdire une ressource est de ne jamais l'accorder,
    donc d'énumérer tout le reste.
    """

    def test_secrets_absents(self, cluster_role_strict):
        for regle in cluster_role_strict["rules"]:
            assert "secrets" not in regle["resources"]

    def test_certificaterequests_absents(self, cluster_role_strict):
        """Ces objets portent des clés privées."""
        for regle in cluster_role_strict["rules"]:
            assert "certificaterequests" not in regle["resources"]

    def test_aucun_joker(self, cluster_role_strict):
        """Un joker réautoriserait les Secrets : le RBAC est additif."""
        for regle in cluster_role_strict["rules"]:
            assert "*" not in regle.get("apiGroups", [])
            assert "*" not in regle["resources"]

    def test_seuls_des_verbes_de_lecture(self, cluster_role_strict):
        autorises = {"get", "list", "watch"}
        for regle in cluster_role_strict["rules"]:
            assert set(regle["verbs"]) <= autorises

    @pytest.mark.parametrize("ressource", [
        "pods", "pods/log", "services", "endpoints", "nodes",
        "persistentvolumeclaims", "deployments", "statefulsets", "jobs",
    ])
    def test_ressources_surveillees_presentes(self, cluster_role_strict, ressource):
        """La variante stricte doit couvrir ce que l'agent surveille."""
        toutes = {r for regle in cluster_role_strict["rules"]
                  for r in regle["resources"]}
        assert ressource in toutes

    def test_metriques_accessibles(self, cluster_role_strict):
        """Sans metrics.k8s.io, la détection de pression mémoire est aveugle."""
        groupes = {g for regle in cluster_role_strict["rules"]
                   for g in regle.get("apiGroups", [])}
        assert "metrics.k8s.io" in groupes

    def test_meme_identite_que_la_variante_par_defaut(self, cluster_role_strict,
                                                      cluster_role):
        """Les deux variantes doivent être interchangeables sans rien casser."""
        assert cluster_role_strict["metadata"]["name"] == cluster_role["metadata"]["name"]
