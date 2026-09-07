"""Évaluation du pipeline RAG : un score reproductible.

Mesure DEUX choses séparément, parce qu'un score global ne dit pas
où est le problème :

  1. RETRIEVAL  — le bon chunk est-il dans le top-K ?
     Métrique : Hit Rate (le bon chunk est présent)
                MRR (à quel rang ? 1er = 1.0, 2e = 0.5, 3e = 0.33)

  2. GÉNÉRATION — la réponse contient-elle ce qu'il faut,
                  et surtout PAS ce qu'il ne faut pas ?

Usage :
    uv run python src/evaluer.py
    uv run python src/evaluer.py --retrieval-seul   # rapide, sans LLM
"""

import json
import sys
import time
import unicodedata
from pathlib import Path

import ollama
import torch
import yaml
from sentence_transformers import SentenceTransformer

import chunk_structure
import rag

CAS = Path("eval/questions.yaml")
# Deux fichiers distincts : eval-fast ne doit pas écraser les réponses
# générées par une évaluation complète — ce sont elles qu'on relit
# quand un cas échoue.
RESULTATS = Path("eval/resultats.json")
RESULTATS_RAPIDE = Path("eval/resultats-retrieval.json")


def normaliser(texte: str) -> str:
    """Minuscules sans accents, pour comparer sans se faire piéger."""
    texte = unicodedata.normalize("NFD", texte.lower())
    return "".join(c for c in texte if unicodedata.category(c) != "Mn")


def evaluer_retrieval(cas: dict, classement: list[str]) -> dict:
    """Le bon chunk est-il remonté, et à quel rang ?"""
    attendus = cas.get("chunk_attendu") or []

    # Cas "absent" : aucun chunk n'est attendu, le retrieval ne peut pas
    # échouer. On ne le compte pas dans les métriques de recherche.
    if not attendus:
        return {"applicable": False}

    top = classement[: rag.TOP_K]
    hit = any(t in attendus for t in top)
    rang = next((i + 1 for i, t in enumerate(top) if t in attendus), None)

    return {
        "applicable": True,
        "hit": hit,
        "rang": rang,
        "rr": 1.0 / rang if rang else 0.0,
        "top": top,
    }


def evaluer_generation(cas: dict, reponse: str) -> dict:
    """La réponse contient-elle ce qu'il faut, et pas ce qu'il ne faut pas ?"""
    r = normaliser(reponse)

    # Une entrée peut être une chaîne (obligatoire) ou une liste
    # d'alternatives (au moins une doit être présente) — le modèle peut
    # répondre en français ou reprendre le terme anglais du corpus.
    manquants = []
    for attendu in cas.get("doit_contenir", []):
        alternatives = attendu if isinstance(attendu, list) else [attendu]
        if not any(normaliser(a) in r for a in alternatives):
            manquants.append(alternatives)

    interdits = [t for t in cas.get("ne_doit_pas_contenir", []) if normaliser(t) in r]

    return {
        "ok": not manquants and not interdits,
        "manquants": manquants,
        "interdits": interdits,
    }


def main() -> None:
    retrieval_seul = "--retrieval-seul" in sys.argv

    cas_tests = yaml.safe_load(CAS.read_text())
    chunks = chunk_structure.decouper(Path(rag.DOCUMENT).read_text())
    titres = [c["titre"] for c in chunks]

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    encodeur = SentenceTransformer(rag.MODELE_EMB, device=device)
    vecteurs = encodeur.encode([c["texte"] for c in chunks], normalize_embeddings=True)

    resultats = []
    debut_total = time.time()

    for cas in cas_tests:
        vec_q = encodeur.encode(cas["question"], normalize_embeddings=True)
        scores = vecteurs @ vec_q
        ordre = sorted(range(len(chunks)), key=lambda i: scores[i], reverse=True)
        classement = [titres[i] for i in ordre]

        res = {
            "id": cas["id"],
            "type": cas["type"],
            "retrieval": evaluer_retrieval(cas, classement),
            "score_max": float(scores[ordre[0]]),
        }

        if not retrieval_seul:
            contexte = rag.construire_contexte(chunks, ordre[: rag.TOP_K])
            rep = ollama.chat(
                model=rag.MODELE_LLM,
                messages=[
                    {"role": "system", "content": rag.SYSTEM},
                    {"role": "user", "content": f"CONTEXTE :\n\n{contexte}\n\n---\n\nQUESTION : {cas['question']}"},
                ],
                options={"temperature": 0.1},
            )
            texte = rep["message"]["content"]
            res["generation"] = evaluer_generation(cas, texte)
            res["reponse"] = texte

        resultats.append(res)

    duree = time.time() - debut_total

    # --- Affichage ---
    print(f"\n\033[1m ÉVALUATION \033[0m  {len(cas_tests)} cas · top-{rag.TOP_K}\n")

    for r in resultats:
        ret = r["retrieval"]
        if ret["applicable"]:
            marque_r = f"\033[32mrang {ret['rang']}\033[0m" if ret["hit"] else "\033[31mRATÉ\033[0m"
        else:
            marque_r = "\033[2mn/a\033[0m"

        if "generation" in r:
            gen = r["generation"]
            marque_g = "\033[32mOK\033[0m" if gen["ok"] else "\033[31mÉCHEC\033[0m"
        else:
            marque_g = "\033[2m—\033[0m"

        print(f"  \033[1m{r['id']:<18}\033[0m {r['type']:<8} retrieval: {marque_r:<20} génération: {marque_g}")

        if "generation" in r and not r["generation"]["ok"]:
            if r["generation"]["manquants"]:
                print(f"      \033[31mmanque :\033[0m {r['generation']['manquants']}")
            if r["generation"]["interdits"]:
                print(f"      \033[31minterdit trouvé :\033[0m {r['generation']['interdits']}")
            print(f"      \033[2m« {r['reponse'][:120].replace(chr(10), ' ')}... »\033[0m")

    # --- Métriques ---
    applicables = [r for r in resultats if r["retrieval"]["applicable"]]
    hit_rate = sum(r["retrieval"]["hit"] for r in applicables) / len(applicables)
    mrr = sum(r["retrieval"]["rr"] for r in applicables) / len(applicables)

    print(f"\n\033[1m  RETRIEVAL \033[0m ({len(applicables)} cas applicables)")
    print(f"    Hit Rate @{rag.TOP_K} : \033[1m{hit_rate:.0%}\033[0m")
    print(f"    MRR              : \033[1m{mrr:.3f}\033[0m  (1.0 = toujours en 1re position)")

    if not retrieval_seul:
        ok = sum(r["generation"]["ok"] for r in resultats)
        print(f"\n\033[1m  GÉNÉRATION \033[0m")
        print(f"    Réussite         : \033[1m{ok}/{len(resultats)}\033[0m ({ok / len(resultats):.0%})")

        # Le sous-score qui compte le plus : le refus sur les cas absents.
        absents = [r for r in resultats if r["type"] == "absent"]
        ok_absents = sum(r["generation"]["ok"] for r in absents)
        print(f"    dont refus corrects : \033[1m{ok_absents}/{len(absents)}\033[0m  \033[2m(anti-hallucination)\033[0m")

    print(f"\n\033[2m  {duree:.1f}s\033[0m")

    sortie = RESULTATS_RAPIDE if retrieval_seul else RESULTATS
    sortie.write_text(json.dumps(resultats, indent=2, ensure_ascii=False))
    print(f"\033[2m  → {sortie}\033[0m\n")


if __name__ == "__main__":
    main()
