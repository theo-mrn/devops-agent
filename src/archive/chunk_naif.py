"""Découpage naïf : couper tous les N caractères, sans rien regarder.

C'est la méthode par défaut de beaucoup de tutoriels. Ce script sert à
montrer POURQUOI elle est mauvaise sur de la documentation technique.

Usage :
    uv run python src/chunk_naif.py
"""

from pathlib import Path

DOCUMENT = Path("data/raw/terraform-moved.mdx")
TAILLE = 500      # caractères par chunk
CHEVAUCHEMENT = 50  # caractères repris du chunk précédent


def decouper(texte: str, taille: int, chevauchement: int) -> list[str]:
    """Coupe tous les `taille` caractères, en reprenant `chevauchement`
    caractères du morceau précédent pour ne pas perdre le fil."""
    chunks = []
    debut = 0
    while debut < len(texte):
        chunks.append(texte[debut : debut + taille])
        debut += taille - chevauchement
    return chunks


def diagnostiquer(chunk: str) -> list[str]:
    """Repère les structures cassées par la coupe."""
    problemes = []

    # Un bloc de code s'ouvre et se ferme par ```. Un nombre impair
    # signifie qu'on a coupé au milieu.
    if chunk.count("```") % 2 == 1:
        problemes.append("BLOC DE CODE COUPÉ")

    # Un tableau markdown sans sa ligne d'en-tête est illisible.
    lignes = chunk.splitlines()
    a_ligne_tableau = any(l.strip().startswith("|") for l in lignes)
    a_separateur = any("---" in l and l.strip().startswith("|") for l in lignes)
    if a_ligne_tableau and not a_separateur:
        problemes.append("TABLEAU ORPHELIN (en-tête perdu)")

    # Commencer ou finir en plein milieu d'un mot.
    if chunk and not chunk[0].isspace() and chunk[0].islower():
        problemes.append("commence au milieu d'un mot")

    return problemes


if __name__ == "__main__":
    texte = DOCUMENT.read_text()
    chunks = decouper(texte, TAILLE, CHEVAUCHEMENT)

    print(f"Document : {DOCUMENT}  ({len(texte)} caractères)")
    print(f"Découpage : {TAILLE} car., chevauchement {CHEVAUCHEMENT}")
    print(f"→ {len(chunks)} chunks\n")

    casses = 0
    for i, chunk in enumerate(chunks):
        problemes = diagnostiquer(chunk)
        if problemes:
            casses += 1

        etiquette = f"\033[31m {' · '.join(problemes)} \033[0m" if problemes else "\033[32m ok \033[0m"
        print(f"\033[1m── CHUNK {i} \033[0m({len(chunk)} car.) {etiquette}")
        print(chunk)
        print()

    print(f"\033[1mBilan : {casses}/{len(chunks)} chunks abîmés.\033[0m")
