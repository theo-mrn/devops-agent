"""Génère les exemples du dataset de fine-tuning à partir du corpus.

Principe (celui du README) : on ne fine-tune PAS pour apprendre des faits
— c'est le rôle du RAG. On fine-tune pour apprendre un COMPORTEMENT.

Trois comportements visés, correspondant aux défauts mesurés :

  1. REFUS  — dire « le contexte ne contient pas cette information » quand
     le contexte est thématiquement proche mais factuellement muet.
     C'est le défaut restant : absent_version_moved, absent_ansible.

  2. MÉTHODE — structurer un diagnostic : constater → investiguer avec les
     bonnes commandes → corriger.

  3. SÛRETÉ — proposer l'inspection non-destructive d'abord, encadrer toute
     commande destructive d'une réserve explicite.

Chaque exemple est au format conversationnel (system/user/assistant) avec
le CONTEXTE injecté comme en production — le modèle doit apprendre à
répondre dans les conditions réelles du RAG.

Usage :
    uv run python src/generer_dataset.py --type refus --n 40
    uv run python src/generer_dataset.py --stats
"""

import argparse
import json
import pickle
import random
from pathlib import Path

INDEX = Path("data/index/corpus.pkl")
SORTIE = Path("dataset/exemples.jsonl")

SYSTEM = (
    "Tu es un ingénieur SRE/DevOps expert. Tu réponds uniquement à partir "
    "du contexte fourni, tu privilégies les commandes d'inspection non "
    "destructives, et tu dis explicitement quand le contexte ne contient "
    "pas la réponse."
)

REFUS = "Le contexte fourni ne contient pas cette information."


def charger_chunks() -> list[dict]:
    with INDEX.open("rb") as f:
        return pickle.load(f)["chunks"]


def formater(question: str, contexte: str, reponse: str, etiquette: str) -> dict:
    return {
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"CONTEXTE :\n\n{contexte}\n\n---\n\nQUESTION : {question}"},
            {"role": "assistant", "content": reponse},
        ],
        "_type": etiquette,
    }


def contexte_depuis(chunks: list[dict]) -> str:
    return "\n\n---\n\n".join(
        f"[Source : {c['source']}/{c['fichier']} — {c['titre']}]\n{c['texte']}"
        for c in chunks
    )


# ── Type 1 : REFUS ────────────────────────────────────────────────
# Le cas difficile : un contexte qui parle DU BON SUJET mais ne répond
# pas à la question posée. C'est exactement absent_version_moved.

# Les questions sont ANCRÉES dans le sujet réel du chunk fourni : c'est
# ce qui rend le cas difficile et réaliste. Une question vague
# (« cette fonctionnalité ») apprendrait un comportement qui ne se
# transfère pas aux questions concrètes de production.
GABARITS_REFUS = [
    "Depuis quelle version de {outil} {sujet} est-il disponible ?",
    "Quelle est la date de sortie de {sujet} ?",
    "Combien coûte l'utilisation de {sujet} ?",
    "Qui maintient le code de {sujet} ?",
    "Quel numéro de CVE affecte {sujet} ?",
    "Quels sont les benchmarks de performance de {sujet} ?",
    "Quelle est la roadmap prévue pour {sujet} ?",
    "Combien d'entreprises utilisent {sujet} en production ?",
    "Quelle licence couvre {sujet} ?",
    "Dans quelle région AWS {sujet} est-il disponible ?",
]

# Association source → nom de l'outil, pour formuler des questions
# crédibles.
OUTILS = {
    "k8s": "Kubernetes", "tf": "Terraform",
    "docker": "Docker", "ci": "GitHub Actions", "interne": "cet outil",
}


def _outil(source: str) -> str:
    for prefixe, nom in OUTILS.items():
        if source.startswith(prefixe):
            return nom
    return "cet outil"


def _sujet(chunk: dict) -> str:
    """Titre de section nettoyé, utilisable dans une question."""
    titre = chunk["titre"]
    titre = titre.split("{")[0].strip()          # retire les ancres {#...}
    titre = titre.split("(")[0].strip()          # retire les « (2/3) »
    return titre if titre and titre != "(préambule)" else chunk["fichier"]


def generer_refus(chunks: list[dict], n: int) -> list[dict]:
    """Contexte thématiquement pertinent, question sans réponse dedans.

    C'est le cas qui résiste depuis la brique 10 : le corpus parle
    abondamment du bloc `moved` sans jamais donner sa version, et le
    modèle comble ce trou précis.
    """
    exemples = []
    rng = random.Random(42)
    utilisables = [c for c in chunks if len(c["texte"]) > 300]

    for i in range(n):
        principal = utilisables[rng.randrange(len(utilisables))]
        # Les deux autres chunks viennent de la MÊME source : le contexte
        # doit être cohérent thématiquement, sinon le cas est trop facile.
        memes = [c for c in utilisables if c["source"] == principal["source"]]
        selection = [principal] + rng.sample(memes, k=min(2, len(memes)))

        gabarit = GABARITS_REFUS[i % len(GABARITS_REFUS)]
        question = gabarit.format(
            outil=_outil(principal["source"]), sujet=_sujet(principal)
        )
        exemples.append(
            formater(question, contexte_depuis(selection), REFUS, "refus")
        )
    return exemples


# ── Type 2 : MÉTHODE ──────────────────────────────────────────────
# Structurer un diagnostic : constater → investiguer → corriger.
# Les réponses sont écrites à la main : c'est le comportement qu'on
# veut enseigner, il ne peut pas être généré par le modèle qu'on corrige.

EXEMPLES_METHODE = [
    {
        "question": "Un pod est en CrashLoopBackOff avec l'exit code 137. Que faire ?",
        "fichiers": ["exit-codes", "etats-pod"],
        "reponse": """### Diagnostic

L'exit code 137 correspond à `128 + 9` : le conteneur a reçu SIGKILL. Dans un
contexte Kubernetes, la cause quasi systématique est un dépassement de la limite
mémoire (OOMKilled). Un dépassement de la limite **CPU** ne tue jamais un
conteneur — il provoque du throttling.

### 1. Confirmer la cause

```bash
kubectl describe pod <pod> -n <namespace>
```

Chercher dans la sortie :

```
    Last State:     Terminated
      Reason:       OOMKilled
      Exit Code:    137
```

### 2. Lire les logs de l'instance qui a crashé

```bash
kubectl logs <pod> -n <namespace> --previous --tail=100
```

Le flag `--previous` est indispensable : sans lui, on lit les logs du conteneur
qui vient de redémarrer, pas de celui qui a échoué.

### 3. Comparer consommation et limite

```bash
kubectl top pod <pod> -n <namespace>
kubectl get pod <pod> -o jsonpath='{.spec.containers[*].resources}'
```

### 4. Corriger

Deux causes à distinguer avant d'agir :

- **Limite trop basse** — augmenter `resources.limits.memory`.
- **Fuite mémoire applicative** — la consommation croît jusqu'à la limite à
  chaque cycle. Augmenter la limite ne fait que retarder le crash ; il faut
  corriger l'application.

Le signe distinctif d'une fuite est un OOMKilled qui se répète à intervalle
régulier après chaque redémarrage.""",
    },
    {
        "question": "Un service systemd ne démarre pas, comment investiguer ?",
        "fichiers": ["linux-diagnostic"],
        "reponse": """### 1. Constater l'état

```bash
systemctl status <service> --no-pager -l
```

Le champ `Main PID:` donne le code de sortie. Quelques codes parlants :
`status=203/EXEC` (binaire introuvable ou non exécutable), `status=200/CHDIR`
(`WorkingDirectory` inexistant), `status=209/STDOUT` (problème de redirection).

### 2. Lire le journal

```bash
journalctl -u <service> -n 100 --no-pager
journalctl -u <service> --since "10 min ago"
```

### 3. Vérifier l'unité elle-même

```bash
systemctl cat <service>
systemd-analyze verify /etc/systemd/system/<service>.service
```

### 4. Après toute modification

```bash
systemctl daemon-reload
systemctl restart <service>
```

Sans `daemon-reload`, systemd continue d'utiliser l'ancienne définition — c'est
l'oubli le plus fréquent.""",
    },
    {
        "question": "Un conteneur Docker s'arrête avec exit code 137, est-ce un OOM ?",
        "fichiers": ["docker-diagnostic"],
        "reponse": """### 1. Confirmer la cause

```bash
docker inspect <conteneur> --format '{{.State.OOMKilled}}'
```

Ce champ tranche : `true` pour un dépassement de la limite mémoire, `false` pour
un arrêt provoqué autrement (`docker kill`, arrêt du daemon).

### 2. Comparer limite et consommation

```bash
docker inspect <conteneur> --format '{{.HostConfig.Memory}}'
docker stats --no-stream <conteneur>
```

Une valeur `0` pour `Memory` signifie qu'aucune limite n'est définie : le
conteneur peut alors épuiser la mémoire de l'hôte et déclencher le OOM killer
du noyau.

### 3. Lire les logs

```bash
docker logs <conteneur> --tail 100
```

Contrairement à Kubernetes, Docker conserve les logs du conteneur arrêté tant
qu'il n'est pas supprimé : il n'existe pas d'équivalent de `--previous`.""",
    },
]


# ── Type 3 : SÛRETÉ ───────────────────────────────────────────────
# Inspection non destructive d'abord ; toute commande destructive
# encadrée d'une réserve explicite et placée en dernier recours.

EXEMPLES_SURETE = [
    {
        "question": "Un pod est bloqué en Terminating depuis 10 minutes, que faire ?",
        "fichiers": ["etats-pod"],
        "reponse": """### 1. Identifier ce qui retient le pod

```bash
kubectl get pod <pod> -n <namespace> -o jsonpath='{.metadata.finalizers}'
kubectl describe pod <pod> -n <namespace>
```

Un pod bloqué en `Terminating` est presque toujours retenu par un finalizer ou
par un volume qui ne se démonte pas.

### 2. Vérifier côté nœud

```bash
kubectl logs -n kube-system <pod-kubelet> --tail=50
```

### 3. En dernier recours seulement

Si les étapes précédentes n'ont pas résolu le blocage :

```bash
kubectl delete pod <pod> -n <namespace> --grace-period=0 --force
```

⚠️ Cette commande retire l'objet de l'API **sans attendre** que le kubelet
confirme l'arrêt du conteneur. Le conteneur peut continuer à tourner sur le
nœud et ses volumes rester montés. Sur un StatefulSet, elle peut provoquer une
double écriture si le pod est recréé pendant que l'ancien tourne encore.

À n'utiliser qu'après avoir identifié la cause du blocage.""",
    },
    {
        "question": "Le disque est plein à cause de Docker, comment libérer de l'espace ?",
        "fichiers": ["docker-diagnostic"],
        "reponse": """### 1. Mesurer avant d'agir

```bash
docker system df
docker system df -v
```

La vue détaillée montre quelles images et quels volumes occupent réellement
l'espace, et lesquels sont encore référencés.

### 2. Nettoyage ciblé

```bash
docker container prune    # conteneurs arrêtés
docker image prune        # images sans tag
```

Ces deux commandes ne touchent qu'à ce qui est manifestement inutilisé.

### 3. Nettoyage agressif — avec réserve

```bash
docker system prune -a
```

⚠️ Cette commande supprime **toutes** les images non utilisées par un conteneur
en cours d'exécution. Sur un hôte de build, elle peut effacer des images qu'il
faudra retélécharger, allongeant fortement les builds suivants.

Toujours inspecter `docker system df -v` avant de l'exécuter.""",
    },
    {
        "question": "Comment annuler un déploiement Kubernetes qui s'est mal passé ?",
        "fichiers": ["deployment"],
        "reponse": """### 1. Constater l'état du rollout

```bash
kubectl rollout status deployment/<nom> -n <namespace>
kubectl rollout history deployment/<nom> -n <namespace>
```

### 2. Revenir à la révision précédente

```bash
kubectl rollout undo deployment/<nom> -n <namespace>
```

Pour cibler une révision précise :

```bash
kubectl rollout undo deployment/<nom> --to-revision=<n> -n <namespace>
```

### 3. Vérifier

```bash
kubectl rollout status deployment/<nom> -n <namespace>
kubectl get pods -l app=<nom> -n <namespace>
```

Ne pas supprimer le Deployment pour le recréer : `rollout undo` conserve
l'historique des révisions et permet de revenir en arrière à nouveau si
nécessaire.""",
    },
]


def generer_manuels(chunks: list[dict], sources: list[dict], etiquette: str) -> list[dict]:
    """Construit les exemples écrits à la main, avec un contexte réel
    tiré du corpus — le modèle doit s'entraîner dans les conditions du RAG."""
    exemples = []
    for spec in sources:
        pertinents = [c for c in chunks if c["fichier"] in spec["fichiers"]]
        if not pertinents:
            print(f"  \033[33m⚠\033[0m aucun chunk pour {spec['fichiers']}")
            continue
        selection = pertinents[:3]
        exemples.append(
            formater(spec["question"], contexte_depuis(selection),
                     spec["reponse"], etiquette)
        )
    return exemples


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--type", choices=["refus", "methode", "surete", "tous"],
                    default="tous")
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()

    if args.stats:
        if not SORTIE.exists():
            print("Aucun dataset généré pour l'instant.")
            return
        lignes = [json.loads(l) for l in SORTIE.read_text().splitlines() if l.strip()]
        par_type: dict[str, int] = {}
        for l in lignes:
            par_type[l.get("_type", "?")] = par_type.get(l.get("_type", "?"), 0) + 1
        print(f"\n\033[1m DATASET \033[0m  {len(lignes)} exemples\n")
        for t, c in sorted(par_type.items(), key=lambda x: -x[1]):
            print(f"  {t:<12} {c:>4}")
        return

    chunks = charger_chunks()
    exemples: list[dict] = []

    if args.type in ("refus", "tous"):
        exemples.extend(generer_refus(chunks, args.n))
    if args.type in ("methode", "tous"):
        exemples.extend(generer_manuels(chunks, EXEMPLES_METHODE, "methode"))
    if args.type in ("surete", "tous"):
        exemples.extend(generer_manuels(chunks, EXEMPLES_SURETE, "surete"))

    SORTIE.parent.mkdir(exist_ok=True)
    with SORTIE.open("a") as f:
        for ex in exemples:
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")

    print(f"{len(exemples)} exemples ajoutés → {SORTIE}")


if __name__ == "__main__":
    main()
