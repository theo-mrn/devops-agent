"""Découpage structurel : couper aux frontières sémantiques du document.

Principe : un document markdown a déjà une structure (titres, blocs de
code, tableaux). On s'en sert au lieu de compter des caractères.

Trois règles :
  1. On retire le frontmatter (métadonnées, pur bruit).
  2. On coupe aux titres `##`, jamais à l'intérieur d'une section.
  3. Les blocs ``` sont atomiques : on ne coupe jamais dedans.

Chaque chunk garde en tête le titre de sa section, pour rester
compréhensible isolément.

Usage :
    uv run python src/chunk_structure.py
"""

import re
from pathlib import Path

DOCUMENT = Path("data/raw/terraform-moved.mdx")
TAILLE_MAX = 1200  # limite haute, mais on ne coupe jamais une structure


def retirer_frontmatter(texte: str) -> str:
    """Supprime le bloc --- ... --- en tête du fichier."""
    if texte.startswith("---"):
        fin = texte.find("---", 3)
        if fin != -1:
            return texte[fin + 3 :].lstrip()
    return texte


def masquer_blocs_code(texte: str) -> tuple[str, list[str]]:
    """Remplace chaque bloc ``` par un jeton, pour qu'aucune règle de
    découpage ne puisse couper à l'intérieur. On les restaure après."""
    blocs: list[str] = []

    def remplacer(m: re.Match) -> str:
        blocs.append(m.group(0))
        return f"\x00BLOC{len(blocs) - 1}\x00"

    return re.sub(r"```.*?```", remplacer, texte, flags=re.DOTALL), blocs


def restaurer_blocs_code(texte: str, blocs: list[str]) -> str:
    for i, bloc in enumerate(blocs):
        texte = texte.replace(f"\x00BLOC{i}\x00", bloc)
    return texte


def decouper(texte: str) -> list[dict]:
    texte = retirer_frontmatter(texte)
    texte, blocs = masquer_blocs_code(texte)

    # Découpe aux titres de niveau 2, en gardant le titre avec sa section.
    morceaux = re.split(r"\n(?=## )", texte)

    chunks: list[dict] = []
    for morceau in morceaux:
        morceau = restaurer_blocs_code(morceau, blocs).strip()
        if not morceau:
            continue

        titres = re.findall(r"^#+ (.+)$", morceau, re.MULTILINE)
        chunks.append({
            "titre": titres[0] if titres else "(préambule)",
            "texte": morceau,
        })

    return chunks


def diagnostiquer(chunk: str) -> list[str]:
    problemes = []
    if chunk.count("```") % 2 == 1:
        problemes.append("BLOC DE CODE COUPÉ")
    lignes = chunk.splitlines()
    if any(l.strip().startswith("|") for l in lignes) and not any(
        "---" in l and l.strip().startswith("|") for l in lignes
    ):
        problemes.append("TABLEAU ORPHELIN")
    if len(chunk) > TAILLE_MAX:
        problemes.append(f"long ({len(chunk)} car.)")
    return problemes


if __name__ == "__main__":
    texte = DOCUMENT.read_text()
    chunks = decouper(texte)

    print(f"Document : {DOCUMENT}  ({len(texte)} caractères)")
    print(f"Découpage : par section markdown, blocs de code atomiques")
    print(f"→ {len(chunks)} chunks\n")

    casses = 0
    for i, c in enumerate(chunks):
        problemes = diagnostiquer(c["texte"])
        if problemes:
            casses += 1
        etiquette = (
            f"\033[31m {' · '.join(problemes)} \033[0m" if problemes else "\033[32m ok \033[0m"
        )
        print(f"\033[1m── CHUNK {i} · {c['titre']} \033[0m({len(c['texte'])} car.) {etiquette}")
        print(c["texte"])
        print()

    print(f"\033[1mBilan : {casses}/{len(chunks)} chunks abîmés.\033[0m")
