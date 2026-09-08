"""API d'ingestion documentaire.

Le but : qu'une entreprise ajoute sa documentation interne sans cloner
de dépôt, sans écrire de YAML et sans connaître la base.

    curl -X POST http://agent/documents \\
         -H "Authorization: Bearer $JETON" \\
         -H "Content-Type: application/json" \\
         -d '{"source": "runbooks", "fichier": "postgres.md",
              "texte": "# Restaurer une sauvegarde\\n..."}'

Le CronJob Git existant reste valable : il devient un client de cette
API parmi d'autres, au lieu d'en être l'unique porte d'entrée.

Sur le choix de `http.server` plutôt que FastAPI : quatre routes ne
justifient pas ~40 Mo de dépendances dans une image qui en fait 71.
`psycopg` étant déjà en dépendance de base, l'API tourne dans l'image
légère — l'encodage, lui, est délégué (voir vectordb.encoder).
"""

from __future__ import annotations

import hmac
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from devops_agent.ingestion import chunking
from devops_agent.retrieval import vectordb

# Jeton d'accès. Sans lui l'API refuse de démarrer : une porte
# d'écriture sur l'index ne doit jamais être ouverte par défaut.
JETON = os.environ.get("RAG_API_JETON", "")

# Un document dépassant cette taille est refusé plutôt que de faire
# gonfler la mémoire du pod sans limite.
TAILLE_MAX = int(os.environ.get("RAG_API_TAILLE_MAX", str(4 * 1024 * 1024)))

# Sources que l'API n'a pas le droit de toucher : elles sont
# reconstruites par le CronJob, un envoi manuel serait écrasé au
# prochain passage — autant le dire tout de suite.
SOURCES_RESERVEES = {"infra", "runbook"}


def _valide(nom: str) -> bool:
    """Un nom de source ou de fichier sûr et lisible."""
    return (
        bool(nom)
        and len(nom) <= 200
        and ".." not in nom
        and not nom.startswith("/")
        and all(c.isalnum() or c in "-_./ " for c in nom)
    )


class Handler(BaseHTTPRequestHandler):
    """Routes : POST, DELETE et GET /documents, GET /sources, GET /sante."""

    def log_message(self, forme, *args):  # noqa: A002
        # Les journaux par défaut écrivent sur stderr sans horodatage
        # exploitable ; on préfixe pour que ce soit filtrable.
        print(f"api {forme % args}", file=sys.stderr)

    # ── Réponses ──────────────────────────────────────────────────

    def _repondre(self, code: int, corps: dict) -> None:
        donnees = json.dumps(corps, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(donnees)))
        self.end_headers()
        self.wfile.write(donnees)

    def _autorise(self) -> bool:
        """Compare le jeton à temps constant, pour ne pas le divulguer."""
        entete = self.headers.get("Authorization", "")
        propose = entete[7:] if entete.startswith("Bearer ") else ""
        if hmac.compare_digest(propose, JETON):
            return True
        self._repondre(401, {"erreur": "jeton absent ou invalide"})
        return False

    def _corps(self) -> dict | None:
        taille = int(self.headers.get("Content-Length") or 0)
        if taille > TAILLE_MAX:
            self._repondre(413, {
                "erreur": f"document trop volumineux (max {TAILLE_MAX} octets)"
            })
            return None
        try:
            return json.loads(self.rfile.read(taille) or b"{}")
        except json.JSONDecodeError as e:
            self._repondre(400, {"erreur": f"JSON invalide : {e}"})
            return None

    # ── Routes ────────────────────────────────────────────────────

    def do_GET(self) -> None:  # noqa: N802
        chemin = self.path.split("?")[0].rstrip("/")

        if chemin == "/sante":
            # Volontairement sans authentification : c'est la sonde de
            # Kubernetes, et elle ne divulgue rien.
            self._repondre(200, {"base": vectordb.disponible()})
            return

        if chemin == "/sources":
            if not self._autorise():
                return
            try:
                documents = vectordb.inventaire()
            except Exception as e:
                self._repondre(503, {"erreur": f"base indisponible : {e}"})
                return

            # Regroupe par source, en disant qui l'alimente : sans cela
            # impossible de savoir si l'on peut y écrire ou si le
            # CronJob l'écrasera au prochain passage.
            sources: dict[str, dict] = {}
            for doc in documents:
                s = sources.setdefault(doc["source"], {
                    "source": doc["source"], "documents": 0, "chunks": 0,
                    "indexe_le": None,
                })
                s["documents"] += 1
                s["chunks"] += doc["chunks"]
                if doc["indexe_le"] and (s["indexe_le"] is None
                                         or doc["indexe_le"] > s["indexe_le"]):
                    s["indexe_le"] = doc["indexe_le"]

            for s in sources.values():
                reservee = s["source"] in SOURCES_RESERVEES
                s["alimentee_par"] = "cronjob" if reservee else "api"
                s["modifiable_par_api"] = not reservee

            self._repondre(200, {
                "sources": sorted(sources.values(), key=lambda x: x["source"]),
                "total": len(sources),
            })
            return

        if chemin != "/documents":
            self._repondre(404, {"erreur": "route inconnue"})
            return
        if not self._autorise():
            return

        source = None
        if "?" in self.path and "source=" in self.path:
            source = self.path.split("source=")[1].split("&")[0]

        try:
            documents = vectordb.inventaire(source)
        except Exception as e:
            self._repondre(503, {"erreur": f"base indisponible : {e}"})
            return
        self._repondre(200, {"documents": documents, "total": len(documents)})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?")[0].rstrip("/") != "/documents":
            self._repondre(404, {"erreur": "route inconnue"})
            return
        if not self._autorise():
            return
        if (corps := self._corps()) is None:
            return

        source = str(corps.get("source", "")).strip()
        fichier = str(corps.get("fichier", "")).strip()
        texte = corps.get("texte", "")

        if not _valide(source) or not _valide(fichier):
            self._repondre(400, {
                "erreur": "source et fichier requis : alphanumériques, "
                          "tirets, points et barres obliques"
            })
            return
        if source in SOURCES_RESERVEES:
            self._repondre(409, {
                "erreur": f"« {source} » est reconstruite par le CronJob ; "
                          "un envoi serait écrasé. Choisir un autre nom."
            })
            return
        if not isinstance(texte, str) or not texte.strip():
            self._repondre(400, {"erreur": "texte vide"})
            return

        morceaux = chunking.decouper(texte)
        if not morceaux:
            self._repondre(400, {"erreur": "aucun contenu exploitable"})
            return

        vecteurs = []
        for morceau in morceaux:
            vecteur = vectordb.encoder(morceau["texte"])
            if vecteur is None:
                # Sans modèle d'embedding, rien ne doit être écrit :
                # un chunk sans vecteur serait invisible à la recherche.
                self._repondre(503, {
                    "erreur": "encodage indisponible — vérifier RAG_EMBED_URL "
                              "ou déployer l'image -rag"
                })
                return
            vecteurs.append(vecteur)

        try:
            supprimes, inseres = vectordb.remplacer_document(
                source, fichier, morceaux, vecteurs
            )
        except Exception as e:
            self._repondre(503, {"erreur": f"écriture impossible : {e}"})
            return

        self._repondre(200, {
            "source": source, "fichier": fichier,
            "chunks": inseres, "remplaces": supprimes,
        })

    def do_DELETE(self) -> None:  # noqa: N802
        if self.path.split("?")[0].rstrip("/") != "/documents":
            self._repondre(404, {"erreur": "route inconnue"})
            return
        if not self._autorise():
            return
        if (corps := self._corps()) is None:
            return

        source = str(corps.get("source", "")).strip()
        fichier = str(corps.get("fichier", "")).strip()
        if not _valide(source) or not _valide(fichier):
            self._repondre(400, {"erreur": "source et fichier requis"})
            return

        try:
            otes = vectordb.supprimer_document(source, fichier)
        except Exception as e:
            self._repondre(503, {"erreur": f"suppression impossible : {e}"})
            return
        self._repondre(200, {"source": source, "fichier": fichier,
                             "chunks_supprimes": otes})


def servir(port: int = 8080) -> int:
    """Démarre l'API. Point d'entrée de `devops-agent api`."""
    if not JETON:
        print("RAG_API_JETON absent : l'API n'ouvre pas d'accès en écriture "
              "sans jeton.\n\n"
              "  kubectl create secret generic agent-api \\\n"
              "    --from-literal=jeton=$(openssl rand -hex 32) -n devops-agent",
              file=sys.stderr)
        return 1

    vectordb.initialiser()
    serveur = ThreadingHTTPServer(("", port), Handler)
    print(f"API d'ingestion sur :{port}", file=sys.stderr)
    try:
        serveur.serve_forever()
    except KeyboardInterrupt:
        print("arrêt", file=sys.stderr)
    return 0
