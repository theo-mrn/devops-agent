"""Index vectoriel dans PostgreSQL, via pgvector.

Le fichier `corpus.pkl` ne convient pas à un déploiement industriel :

  - il contient le TEXTE des chunks, donc les manifests d'un dépôt privé,
    ce qui interdit de l'embarquer dans une image publique ;
  - le lire suppose de charger PyTorch et sentence-transformers dans le
    pod, soit ~2,5 Go d'image pour un agent qui pèse 66 Mo ;
  - il faut le régénérer et le redéployer à chaque changement du dépôt.

Avec pgvector, l'index vit dans le cluster. Un job l'alimente, l'agent
l'interroge en SQL. Rien ne sort, rien n'est embarqué.

L'agent doit encore encoder la QUESTION — une seule phrase, ce qui ne
justifie toujours pas 2,5 Go. Deux options, choisies par configuration :

    RAG_EMBED_URL   un service d'embedding joignable dans le cluster
    (à défaut)      sentence-transformers en local, si installé

Sans l'un ni l'autre, la recherche est simplement indisponible et l'agent
poursuit avec ses autres outils.
"""

import json
import os
import urllib.request
from dataclasses import dataclass

# Chaîne de connexion, au format attendu par psycopg.
DSN = os.environ.get("RAG_DB_DSN", "")

# Service d'embedding HTTP. Il expose l'unique opération dont l'agent a
# besoin : transformer une question en vecteur.
EMBED_URL = os.environ.get("RAG_EMBED_URL", "")

# Dimension des vecteurs : celle de bge-m3. Changer de modèle d'embedding
# impose de recréer la table.
DIMENSION = int(os.environ.get("RAG_EMBED_DIM", "1024"))

TABLE = os.environ.get("RAG_DB_TABLE", "chunks")


@dataclass
class Chunk:
    """Un extrait retrouvé, avec sa provenance."""
    texte: str
    titre: str
    source: str
    fichier: str
    score: float


def disponible() -> bool:
    """La recherche par base est-elle utilisable ?"""
    if not DSN:
        return False
    try:
        import psycopg  # noqa: F401
        return True
    except ImportError:
        return False


def _connexion():
    import psycopg
    return psycopg.connect(DSN)


# ── Schéma ───────────────────────────────────────────────────────

SCHEMA = f"""
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS {TABLE} (
    id          BIGSERIAL PRIMARY KEY,
    texte       TEXT        NOT NULL,
    titre       TEXT        NOT NULL,
    source      TEXT        NOT NULL,
    fichier     TEXT        NOT NULL,
    -- Empreinte du contenu : permet de ne réencoder que ce qui a changé.
    empreinte   TEXT        NOT NULL,
    vecteur     vector({DIMENSION}) NOT NULL,
    indexe_le   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Un même fichier peut porter plusieurs chunks ; l'empreinte les
-- distingue et rend l'insertion idempotente.
CREATE UNIQUE INDEX IF NOT EXISTS {TABLE}_empreinte
    ON {TABLE} (empreinte);

CREATE INDEX IF NOT EXISTS {TABLE}_source ON {TABLE} (source);

-- IVFFlat : recherche approchée, largement suffisante ici et bien plus
-- rapide qu'un balayage complet dès quelques milliers de lignes.
-- `lists` doit valoir environ racine(nombre de lignes).
CREATE INDEX IF NOT EXISTS {TABLE}_vecteur
    ON {TABLE} USING ivfflat (vecteur vector_cosine_ops)
    WITH (lists = 100);
"""


def initialiser() -> None:
    """Crée l'extension, la table et les index s'ils n'existent pas."""
    with _connexion() as conn:
        conn.execute(SCHEMA)
        conn.commit()


# ── Encodage de la question ──────────────────────────────────────

def encoder(texte: str) -> list[float] | None:
    """Transforme un texte en vecteur.

    Passe par un service HTTP si `RAG_EMBED_URL` est défini — c'est ce
    qui évite d'embarquer PyTorch dans le pod de l'agent. À défaut,
    utilise sentence-transformers en local.
    """
    if EMBED_URL:
        corps = json.dumps({"texte": texte}).encode()
        requete = urllib.request.Request(
            EMBED_URL, data=corps,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(requete, timeout=30) as reponse:
                return json.loads(reponse.read())["vecteur"]
        except Exception:
            return None

    try:
        from devops_agent.retrieval import pipeline
        _, encodeur = pipeline.charger()
        return encodeur.encode(texte, normalize_embeddings=True).tolist()
    except Exception:
        return None


# ── Recherche ────────────────────────────────────────────────────

def chercher(question: str, k: int = 5,
             sources: list[str] | None = None) -> list[Chunk]:
    """Recherche les k extraits les plus proches de la question.

    La similarité cosinus est calculée par PostgreSQL : l'agent n'a
    aucun calcul vectoriel à faire.
    """
    vecteur = encoder(question)
    if vecteur is None:
        return []

    filtre = "WHERE source = ANY(%(sources)s)" if sources else ""
    requete = f"""
        SELECT texte, titre, source, fichier,
               1 - (vecteur <=> %(vecteur)s::vector) AS score
        FROM {TABLE}
        {filtre}
        ORDER BY vecteur <=> %(vecteur)s::vector
        LIMIT %(k)s
    """

    with _connexion() as conn:
        lignes = conn.execute(
            requete,
            {"vecteur": str(vecteur), "k": k, "sources": sources},
        ).fetchall()

    return [Chunk(*ligne) for ligne in lignes]


# ── Alimentation ─────────────────────────────────────────────────

def remplacer_source(source: str, chunks: list[dict],
                     vecteurs: list[list[float]]) -> tuple[int, int]:
    """Remplace tout le contenu d'une source.

    Le remplacement se fait dans une transaction : à aucun moment la
    table n'est vide pour cette source, donc l'agent ne voit jamais un
    index incomplet.

    Renvoie (supprimés, insérés).
    """
    import hashlib

    with _connexion() as conn, conn.transaction():
        supprimes = conn.execute(
            f"DELETE FROM {TABLE} WHERE source = %s", (source,)
        ).rowcount

        for chunk, vecteur in zip(chunks, vecteurs):
            empreinte = hashlib.sha256(
                f"{source}:{chunk['fichier']}:{chunk['texte']}".encode()
            ).hexdigest()
            conn.execute(
                f"""INSERT INTO {TABLE}
                        (texte, titre, source, fichier, empreinte, vecteur)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (empreinte) DO NOTHING""",
                (chunk["texte"], chunk["titre"], source,
                 chunk["fichier"], empreinte, str(vecteur)),
            )

    return supprimes, len(chunks)


def statistiques() -> dict[str, int]:
    """Nombre de chunks par source."""
    with _connexion() as conn:
        lignes = conn.execute(
            f"SELECT source, count(*) FROM {TABLE} GROUP BY source ORDER BY 2 DESC"
        ).fetchall()
    return dict(lignes)
