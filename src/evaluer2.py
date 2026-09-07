"""Évaluation sur le corpus complet (387 chunks).

Différence avec `evaluer.py` : les cas ciblent un FICHIER attendu
(`fichier_attendu`) plutôt qu'un titre de section — sur 387 chunks,
raisonner par titre n'a plus de sens.

Usage :
    uv run python src/evaluer2.py
    uv run python src/evaluer2.py --retrieval-seul
"""

import json
import sys
import time
import unicodedata
from pathlib import Path

import ollama
import yaml

import rag2

CAS = Path("eval/questions.yaml")
SORTIE = Path("eval/resultats-corpus.json")
SORTIE_RAPIDE = Path("eval/resultats-corpus-retrieval.json")


def normaliser(texte: str) -> str:
    texte = unicodedata.normalize("NFD", texte.lower())
    return "".join(c for c in texte if unicodedata.category(c) != "Mn")


def verifier_generation(cas: dict, reponse: str) -> dict:
    r = normaliser(reponse)
    manquants = []
    for attendu in cas.get("doit_contenir", []):
        alternatives = attendu if isinstance(attendu, list) else [attendu]
        if not any(normaliser(a) in r for a in alternatives):
            manquants.append(alternatives)
    interdits = [t for t in cas.get("ne_doit_pas_contenir", []) if normaliser(t) in r]
    return {"ok": not manquants and not interdits, "manquants": manquants, "interdits": interdits}


def main() -> None:
    rapide = "--retrieval-seul" in sys.argv
    cas_tests = yaml.safe_load(CAS.read_text())

    resultats = []
    debut = time.time()

    for cas in cas_tests:
        trouves = rag2.chercher_hybride(cas["question"], k=rag2.TOP_K)
        fichiers = [c["fichier"] for c, _ in trouves]

        attendus = cas.get("fichier_attendu") or []
        if attendus:
            rang = next((i + 1 for i, f in enumerate(fichiers) if f in attendus), None)
            ret = {"applicable": True, "hit": rang is not None,
                   "rang": rang, "rr": 1.0 / rang if rang else 0.0}
        else:
            ret = {"applicable": False}

        res = {
            "id": cas["id"], "type": cas["type"], "retrieval": ret,
            "fichiers": fichiers, "score_max": trouves[0][1],
        }

        if not rapide:
            contexte = "\n\n---\n\n".join(
                f"[Source : {c['source']}/{c['fichier']} — {c['titre']}]\n{c['texte']}"
                for c, _ in trouves
            )
            rep = ollama.chat(
                model=rag2.MODELE_LLM,
                messages=[
                    {"role": "system", "content": rag2.SYSTEM},
                    {"role": "user", "content": f"CONTEXTE :\n\n{contexte}\n\n---\n\nQUESTION : {cas['question']}"},
                ],
                options={"temperature": 0.1},
            )
            res["generation"] = verifier_generation(cas, rep["message"]["content"])
            res["reponse"] = rep["message"]["content"]

        resultats.append(res)

    duree = time.time() - debut

    print(f"\n\033[1m ÉVALUATION CORPUS \033[0m  {len(cas_tests)} cas · 387 chunks · top-{rag2.TOP_K}\n")
    for r in resultats:
        ret = r["retrieval"]
        m_r = ("\033[2mn/a\033[0m" if not ret["applicable"]
               else f"\033[32mrang {ret['rang']}\033[0m" if ret["hit"] else "\033[31mRATÉ\033[0m")
        m_g = ("\033[2m—\033[0m" if "generation" not in r
               else "\033[32mOK\033[0m" if r["generation"]["ok"] else "\033[31mÉCHEC\033[0m")
        print(f"  \033[1m{r['id']:<18}\033[0m {r['type']:<8} retr: {m_r:<18} gén: {m_g}")
        if ret["applicable"] and not ret["hit"]:
            print(f"      \033[2mremonté : {r['fichiers']}\033[0m")
        if "generation" in r and not r["generation"]["ok"]:
            if r["generation"]["manquants"]:
                print(f"      \033[31mmanque :\033[0m {r['generation']['manquants']}")
            if r["generation"]["interdits"]:
                print(f"      \033[31minterdit :\033[0m {r['generation']['interdits']}")
            print(f"      \033[2m« {r['reponse'][:110].replace(chr(10), ' ')}... »\033[0m")

    app = [r for r in resultats if r["retrieval"]["applicable"]]
    hr = sum(r["retrieval"]["hit"] for r in app) / len(app)
    mrr = sum(r["retrieval"]["rr"] for r in app) / len(app)
    print(f"\n\033[1m  RETRIEVAL \033[0m ({len(app)} cas)")
    print(f"    Hit Rate @{rag2.TOP_K} : \033[1m{hr:.0%}\033[0m")
    print(f"    MRR              : \033[1m{mrr:.3f}\033[0m")

    if not rapide:
        ok = sum(r["generation"]["ok"] for r in resultats)
        absents = [r for r in resultats if r["type"] == "absent"]
        ok_abs = sum(r["generation"]["ok"] for r in absents)
        print(f"\n\033[1m  GÉNÉRATION \033[0m")
        print(f"    Réussite         : \033[1m{ok}/{len(resultats)}\033[0m ({ok / len(resultats):.0%})")
        print(f"    dont refus       : \033[1m{ok_abs}/{len(absents)}\033[0m \033[2m(anti-hallucination)\033[0m")

    print(f"\n\033[2m  {duree:.1f}s\033[0m")
    cible = SORTIE_RAPIDE if rapide else SORTIE
    cible.write_text(json.dumps(resultats, indent=2, ensure_ascii=False))
    print(f"\033[2m  → {cible}\033[0m\n")


if __name__ == "__main__":
    main()
