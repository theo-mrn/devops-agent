"""Compare baseline et RAG sur la même question, chiffres à l'appui."""

import time

import ollama
import torch
from sentence_transformers import SentenceTransformer
from pathlib import Path

import chunk_structure
import rag

QUESTION = "Quelle est la syntaxe exacte du bloc moved en Terraform ?"

# --- Sans RAG ---
t0 = time.time()
sans = ollama.chat(
    model=rag.MODELE_LLM,
    messages=[
        {"role": "system", "content": "Tu es un ingénieur SRE/DevOps expert."},
        {"role": "user", "content": QUESTION},
    ],
    options={"temperature": 0.1},
)
t_sans = time.time() - t0

# --- Avec RAG ---
chunks = chunk_structure.decouper(Path(rag.DOCUMENT).read_text())
enc = SentenceTransformer(rag.MODELE_EMB, device="mps" if torch.backends.mps.is_available() else "cpu")

t0 = time.time()
vecs = enc.encode([c["texte"] for c in chunks], normalize_embeddings=True)
vq = enc.encode(QUESTION, normalize_embeddings=True)
scores = vecs @ vq
top = sorted(range(len(chunks)), key=lambda i: scores[i], reverse=True)[: rag.TOP_K]
t_recherche = time.time() - t0

contexte = rag.construire_contexte(chunks, top)
t0 = time.time()
avec = ollama.chat(
    model=rag.MODELE_LLM,
    messages=[
        {"role": "system", "content": rag.SYSTEM},
        {"role": "user", "content": f"CONTEXTE :\n\n{contexte}\n\n---\n\nQUESTION : {QUESTION}"},
    ],
    options={"temperature": 0.1},
)
t_gen = time.time() - t0

print(f"{'':<26} {'sans RAG':>12} {'avec RAG':>12}")
print("─" * 52)
print(f"{'tokens en entrée':<26} {sans.get('prompt_eval_count', 0):>12} {avec.get('prompt_eval_count', 0):>12}")
print(f"{'tokens en sortie':<26} {sans.get('eval_count', 0):>12} {avec.get('eval_count', 0):>12}")
print(f"{'temps recherche (s)':<26} {'—':>12} {t_recherche:>12.2f}")
print(f"{'temps génération (s)':<26} {t_sans:>12.2f} {t_gen:>12.2f}")
print(f"{'TOTAL (s)':<26} {t_sans:>12.2f} {t_recherche + t_gen:>12.2f}")
