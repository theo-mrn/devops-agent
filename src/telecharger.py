"""Constitue le corpus : télécharge des pages de doc ciblées.

Principe directeur : PAS de « clone toute la doc ». Un corpus trop gros
noie le signal et rend l'inspection manuelle impossible. On prend des
sections ciblées, sur les sujets que le système doit couvrir.

Usage :
    uv run python src/telecharger.py
"""

import json
import time
import urllib.request
from pathlib import Path

DESTINATION = Path("data/raw")
API = "https://api.github.com/repos/{repo}/contents/{chemin}"
BRUT = "https://raw.githubusercontent.com/{repo}/{branche}/{chemin}"

# Sections retenues, et pourquoi.
SOURCES = [
    # --- Kubernetes : diagnostic de pannes (le cœur du cas d'usage) ---
    ("kubernetes/website", "main", "content/en/docs/tasks/debug/debug-application", "k8s-debug"),
    ("kubernetes/website", "main", "content/en/docs/concepts/workloads/pods", "k8s-pods"),
    ("kubernetes/website", "main", "content/en/docs/concepts/configuration", "k8s-config"),

    # --- Kubernetes : évictions et pression mémoire ---
    # Ajouté en brique 9 : OOMKilled n'apparaissait que dans 1 fichier sur 40,
    # cause racine de l'échec du cas diag_exit137.
    ("kubernetes/website", "main", "content/en/docs/concepts/scheduling-eviction", "k8s-eviction"),

    # --- Kubernetes : diagnostic de cluster et contrôleurs ---
    ("kubernetes/website", "main", "content/en/docs/tasks/debug/debug-cluster", "k8s-cluster"),
    ("kubernetes/website", "main", "content/en/docs/concepts/workloads/controllers", "k8s-workloads"),

    # --- Kubernetes : configuration de pods et conteneurs ---
    ("kubernetes/website", "main", "content/en/docs/tasks/configure-pod-container", "k8s-tasks"),

    # --- Terraform : langage et refactoring ---
    ("hashicorp/web-unified-docs", "main", "content/terraform/v1.11.x/docs/language", "tf-language"),
    ("hashicorp/web-unified-docs", "main", "content/terraform/v1.11.x/docs/language/state", "tf-state"),
]

EXTENSIONS = {".md", ".mdx"}
IGNORER = {"_index.md", "_index.mdx", "index.mdx"}  # pages de sommaire, peu de contenu


def lister(repo: str, chemin: str) -> list[dict]:
    url = API.format(repo=repo, chemin=chemin)
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.load(r)
    except Exception as e:
        print(f"  \033[31m✗\033[0m {chemin} : {e}")
        return []


def telecharger(repo: str, branche: str, chemin: str) -> bytes | None:
    url = BRUT.format(repo=repo, branche=branche, chemin=chemin)
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return r.read()
    except Exception as e:
        print(f"  \033[31m✗\033[0m {chemin} : {e}")
        return None


def main() -> None:
    DESTINATION.mkdir(parents=True, exist_ok=True)
    total, octets = 0, 0

    for repo, branche, chemin, prefixe in SOURCES:
        print(f"\n\033[1m{prefixe}\033[0m  \033[2m{repo}/{chemin}\033[0m")
        entrees = lister(repo, chemin)

        for entree in entrees:
            if entree.get("type") != "file":
                continue
            nom = entree["name"]
            if Path(nom).suffix not in EXTENSIONS or nom in IGNORER:
                continue

            contenu = telecharger(repo, branche, entree["path"])
            if not contenu:
                continue

            # Préfixe le nom pour garder la trace de la source.
            cible = DESTINATION / f"{prefixe}__{nom}"
            cible.write_bytes(contenu)
            total += 1
            octets += len(contenu)
            print(f"  \033[32m✓\033[0m {nom} \033[2m({len(contenu):,} o)\033[0m")

            time.sleep(0.1)  # courtoisie envers l'API

    print(f"\n\033[1m{total} fichiers · {octets / 1024:.0f} Ko\033[0m → {DESTINATION}/")


if __name__ == "__main__":
    main()
