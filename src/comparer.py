"""Compare les deux stratégies de découpage côte à côte."""

from pathlib import Path

import chunk_naif
import chunk_structure

DOCUMENT = Path("data/raw/terraform-moved.mdx")
texte = DOCUMENT.read_text()

naifs = chunk_naif.decouper(texte, chunk_naif.TAILLE, chunk_naif.CHEVAUCHEMENT)
struct = [c["texte"] for c in chunk_structure.decouper(texte)]

def bilan(chunks, diag):
    casses = sum(1 for c in chunks if diag(c))
    tailles = [len(c) for c in chunks]
    return len(chunks), casses, min(tailles), max(tailles)

n1, c1, min1, max1 = bilan(naifs, chunk_naif.diagnostiquer)
n2, c2, min2, max2 = bilan(struct, chunk_structure.diagnostiquer)

print(f"{'':<22} {'naïf':>12} {'structurel':>12}")
print("─" * 48)
print(f"{'chunks':<22} {n1:>12} {n2:>12}")
print(f"{'abîmés':<22} {c1:>12} {c2:>12}")
print(f"{'taille min':<22} {min1:>12} {min2:>12}")
print(f"{'taille max':<22} {max1:>12} {max2:>12}")
print()

# Le test qui compte vraiment : un seul chunk suffit-il à répondre ?
CIBLE = "from = aws_instance.a"
for nom, chunks in [("naïf", naifs), ("structurel", struct)]:
    trouve = [i for i, c in enumerate(chunks) if CIBLE in c]
    print(f"{nom:<12} chunk(s) contenant l'exemple complet : {trouve}")
