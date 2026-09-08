"""Garde-fous de l'API d'ingestion.

L'API est une porte d'écriture ouverte sur l'index : c'est la surface
la plus exposée du projet. Ces tests portent sur ce qui doit tenir même
après un remaniement — la validation des noms et le refus des sources
reconstruites par le CronJob.
"""

from devops_agent.api import documents


class TestValidationNoms:
    """`_valide` filtre ce qui arrive de l'extérieur."""

    def test_accepte_un_chemin_ordinaire(self):
        assert documents._valide("procedures/astreinte.md")
        assert documents._valide("runbooks")
        assert documents._valide("guide interne v2.md")
        assert documents._valide("equipe-infra_2026.md")

    def test_refuse_la_traversee_de_chemin(self):
        assert not documents._valide("../../etc/passwd")
        assert not documents._valide("docs/../../../secret")
        assert not documents._valide("..")

    def test_refuse_un_chemin_absolu(self):
        assert not documents._valide("/etc/passwd")
        assert not documents._valide("/")

    def test_refuse_le_vide(self):
        assert not documents._valide("")

    def test_refuse_un_nom_demesure(self):
        assert not documents._valide("a" * 201)
        assert documents._valide("a" * 200)

    def test_refuse_les_caracteres_de_controle(self):
        # Une injection par retour à la ligne ou octet nul n'a rien à
        # faire dans un nom de fichier.
        assert not documents._valide("doc\nmalveillant.md")
        assert not documents._valide("doc\x00.md")
        assert not documents._valide("doc;rm -rf /.md")
        assert not documents._valide("$(whoami).md")


class TestSourcesReservees:
    """Les sources du CronJob ne s'écrivent pas par l'API.

    Sans ce garde-fou, un envoi serait silencieusement écrasé au
    passage suivant du CronJob — le pire des cas : l'utilisateur croit
    sa doc indexée alors qu'elle a disparu.
    """

    def test_les_sources_du_cronjob_sont_protegees(self):
        assert "infra" in documents.SOURCES_RESERVEES
        assert "runbook" in documents.SOURCES_RESERVEES

    def test_une_source_libre_ne_l_est_pas(self):
        assert "interne" not in documents.SOURCES_RESERVEES
        assert "confluence" not in documents.SOURCES_RESERVEES


class TestConfiguration:
    """L'API ne doit jamais s'ouvrir par accident."""

    def test_pas_de_jeton_par_defaut(self, monkeypatch):
        # `servir` refuse de démarrer sans RAG_API_JETON ; le défaut
        # vide est ce qui rend ce refus possible.
        monkeypatch.delenv("RAG_API_JETON", raising=False)
        import importlib
        recharge = importlib.reload(documents)
        assert recharge.JETON == ""

    def test_taille_maximale_bornee(self):
        # Sans borne, un envoi unique pourrait épuiser la mémoire du pod.
        assert documents.TAILLE_MAX > 0
        assert documents.TAILLE_MAX <= 64 * 1024 * 1024


class TestVueDesSources:
    """`GET /sources` dit qui alimente quoi.

    Deux sources aux noms voisins (« runbook » du CronJob, « runbooks »
    envoyée par API) sont indiscernables dans un simple inventaire.
    Sans cette distinction, une entreprise ne sait pas où elle peut
    écrire sans être écrasée au prochain passage du CronJob.
    """

    def test_les_sources_du_cronjob_sont_bien_celles_attendues(self):
        # Ces deux noms viennent de reindexer.py : s'ils y changent
        # sans être répercutés ici, l'API laisserait écrire dans une
        # source que le CronJob écrase.
        assert documents.SOURCES_RESERVEES == {"infra", "runbook"}

    def test_une_source_applicative_est_modifiable(self):
        assert "runbooks" not in documents.SOURCES_RESERVEES
        assert "confluence" not in documents.SOURCES_RESERVEES

    def test_les_noms_voisins_ne_se_confondent_pas(self):
        # « runbook » est reconstruite, « runbooks » ne l'est pas :
        # un caractère les sépare, d'où l'intérêt de l'afficher.
        assert "runbook" in documents.SOURCES_RESERVEES
        assert "runbooks" not in documents.SOURCES_RESERVEES
