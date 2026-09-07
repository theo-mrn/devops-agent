"""Indexation du corpus complet : plusieurs dizaines de documents.

Différences avec la brique 2, qui ne traitait qu'un fichier :
  - on parcourt tout `data/raw/`,
  - chaque chunk garde sa PROVENANCE (fichier + section) pour être citable,
  - les chunks trop longs sont redécoupés (une section peut faire 20 Ko),
  - les chunks trop courts sont écartés (titres orphelins, bruit).

Usage :
    uv run python src/corpus.py          # construit et sauvegarde l'index
    uv run python src/corpus.py --stats  # statistiques seulement
"""

import pickle
import re
import sys
import time
from pathlib import Path

import torch
from sentence_transformers import SentenceTransformer

import chunk_structure

# Deux sources : la documentation téléchargée, et les documents internes
# rédigés pour combler ce que la doc officielle ne couvre pas (table des
# codes de sortie, procédures de diagnostic).
import config

SOURCES = config.SOURCES
INDEX = config.INDEX
MODELE_EMB = config.EMBEDDING

TAILLE_MAX = config.TAILLE_MAX_CHUNK
TAILLE_MIN = config.TAILLE_MIN_CHUNK


def redecouper_long(chunk: dict) -> list[dict]:
    """Une section de 20 Ko doit être redécoupée — mais aux paragraphes,
    jamais au milieu d'un bloc de code."""
    texte = chunk["texte"]
    if len(texte) <= TAILLE_MAX:
        return [chunk]

    texte_masque, blocs = chunk_structure.masquer_blocs_code(texte)

    def assembler(unites: list[str], separateur: str) -> list[str]:
        morceaux, courant = [], ""
        for u in unites:
            if len(courant) + len(u) > TAILLE_MAX and courant:
                morceaux.append(courant)
                courant = u
            else:
                courant = f"{courant}{separateur}{u}" if courant else u
        if courant:
            morceaux.append(courant)
        return morceaux

    # D'abord par paragraphes. Certains documents n'ont pas de ligne vide :
    # on retombe alors sur un découpage par lignes.
    morceaux = assembler(texte_masque.split("\n\n"), "\n\n")
    if any(len(m) > TAILLE_MAX for m in morceaux):
        raffines = []
        for m in morceaux:
            raffines.extend(assembler(m.split("\n"), "\n") if len(m) > TAILLE_MAX else [m])
        morceaux = raffines

    return [
        {**chunk, "texte": chunk_structure.restaurer_blocs_code(m, blocs).strip(),
         "titre": f"{chunk['titre']} ({i + 1}/{len(morceaux)})" if len(morceaux) > 1 else chunk["titre"]}
        for i, m in enumerate(morceaux)
    ]


def construire_chunks() -> list[dict]:
    fichiers = []
    for racine in SOURCES:
        if racine.exists():
            fichiers.extend(sorted(f for f in racine.iterdir() if f.suffix in {".md", ".mdx"}))

    chunks: list[dict] = []

    for fichier in fichiers:
        # Le préfixe encode la source : "k8s-debug__debug-pods.md".
        # Les documents internes n'en ont pas : ils prennent "interne".
        if "__" in fichier.stem:
            source, _, nom = fichier.stem.partition("__")
        else:
            source, nom = "interne", fichier.stem

        for c in chunk_structure.decouper(fichier.read_text(errors="replace")):
            for morceau in redecouper_long(c):
                if len(morceau["texte"]) < TAILLE_MIN:
                    continue
                chunks.append({
                    "texte": morceau["texte"],
                    "titre": morceau["titre"],
                    "source": source or "?",
                    "fichier": nom or fichier.stem,
                })

    return chunks


def statistiques(chunks: list[dict]) -> None:
    tailles = sorted(len(c["texte"]) for c in chunks)
    n = len(tailles)
    par_source: dict[str, int] = {}
    for c in chunks:
        par_source[c["source"]] = par_source.get(c["source"], 0) + 1

    print(f"\n\033[1m CORPUS \033[0m\n")
    print(f"  fichiers   : {len(set(c['fichier'] for c in chunks))}")
    print(f"  chunks     : {n}")
    print(f"  caractères : {sum(tailles):,}")
    print(f"\n  taille des chunks :")
    print(f"    min / médiane / max : {tailles[0]} / {tailles[n // 2]} / {tailles[-1]}")
    print(f"    moyenne             : {sum(tailles) // n}")
    print(f"\n  par source :")
    for src, cnt in sorted(par_source.items(), key=lambda x: -x[1]):
        barre = "█" * (cnt * 30 // max(par_source.values()))
        print(f"    {src:<14} {cnt:>4}  \033[2m{barre}\033[0m")


def main() -> None:
    chunks = construire_chunks()
    statistiques(chunks)

    if "--stats" in sys.argv:
        return

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"\n  encodage sur {device}...")
    encodeur = SentenceTransformer(MODELE_EMB, device=device)

    debut = time.time()
    vecteurs = encodeur.encode(
        [c["texte"] for c in chunks],
        normalize_embeddings=True,
        batch_size=8,
        show_progress_bar=True,
    )
    duree = time.time() - debut

    INDEX.parent.mkdir(parents=True, exist_ok=True)
    with INDEX.open("wb") as f:
        pickle.dump({
            "chunks": chunks,
            "vecteurs": vecteurs,
            # Permet de détecter un index encodé par un autre modèle.
            "meta": {"embedding": MODELE_EMB, "n_chunks": len(chunks)},
        }, f)

    taille_mo = INDEX.stat().st_size / 1024 / 1024
    print(f"\n  \033[32m✓\033[0m {len(chunks)} chunks encodés en {duree:.1f}s "
          f"({len(chunks) / duree:.1f} chunks/s)")
    print(f"  \033[32m✓\033[0m index : {INDEX} ({taille_mo:.1f} Mo)\n")


if __name__ == "__main__":
    main()
