"""Expansion de requête : combler l'écart entre la langue de l'utilisateur
et le vocabulaire du corpus.

Problème mesuré en brique 13 :

    « Comment planifier une tâche récurrente ? »   → rang —, score 0,016
    « Comment utiliser un CronJob ? »              → rang 1, score 0,779

Le même besoin, exprimé avec le terme technique, passe de l'échec au
rang 1. Un ingénieur français ne dit pas toujours « CronJob » : il dit
« tâche planifiée ». Ni l'embedding ni BM25 ne font ce lien — c'est de
la connaissance métier.

L'expansion AJOUTE les termes techniques à la question sans remplacer la
formulation d'origine : le retrieval garde le sens de la phrase et gagne
les mots-clés exacts.

Usage :
    uv run python src/expansion.py "ta question"
"""

import re
import sys
import unicodedata

# Glossaire : expression courante (souvent française) → termes du corpus.
# Les clés sont normalisées (minuscules, sans accents) au chargement.
GLOSSAIRE: dict[str, list[str]] = {
    # --- Kubernetes : ordonnancement et charges de travail ---
    "tache recurrente": ["CronJob", "schedule", "cron"],
    "tache planifiee": ["CronJob", "schedule"],
    "tache periodique": ["CronJob", "schedule"],
    "planifier une tache": ["CronJob", "schedule"],
    "tache ponctuelle": ["Job", "completions"],
    "sur chaque noeud": ["DaemonSet"],
    "application avec etat": ["StatefulSet"],
    "mise a jour progressive": ["rolling update", "RollingUpdate"],
    "annuler un deploiement": ["rollout undo", "revision"],
    "revenir en arriere": ["rollout undo", "revision"],

    # --- Kubernetes : diagnostic ---
    "noeud notready": ["node conditions", "NotReady", "kubelet"],
    "noeud en panne": ["node conditions", "NotReady"],
    "sante des noeuds": ["node conditions", "node status"],
    "redemarre en boucle": ["CrashLoopBackOff", "restart"],
    "ne demarre pas": ["Pending", "CrashLoopBackOff", "events"],
    "manque de memoire": ["OOMKilled", "OOM", "memory limit"],
    "tue par le systeme": ["OOMKilled", "SIGKILL", "137"],
    "exit code 137": ["OOMKilled", "SIGKILL", "memory limit"],
    "code de sortie 137": ["OOMKilled", "SIGKILL"],
    "exit code 143": ["SIGTERM", "graceful"],
    "expulse": ["Evicted", "eviction", "node pressure"],
    "pod supprime automatiquement": ["Evicted", "eviction"],
    "sonde": ["probe", "livenessProbe", "readinessProbe"],
    "verification de sante": ["probe", "health check"],

    # --- Kubernetes : configuration ---
    "variable d environnement": ["env", "envFrom", "environment variable"],
    "utilisateur non-root": ["runAsUser", "securityContext", "runAsNonRoot"],
    "connaitre son propre nom": ["downward API", "fieldRef", "metadata.name"],
    "volume temporaire": ["emptyDir", "ephemeral"],
    "stockage persistant": ["PersistentVolumeClaim", "PVC"],
    "identite du pod": ["ServiceAccount"],
    "avant l arret": ["preStop", "lifecycle hook"],
    "priorite des pods": ["PriorityClass", "preemption"],

    # --- Docker ---
    "liberer de l espace": ["prune", "pruning", "system df"],
    "disque plein": ["prune", "pruning", "disk usage"],
    "nettoyer docker": ["prune", "pruning"],
    "conteneur sans shell": ["distroless", "namespace", "debug container"],
    "limiter la memoire": ["memory limit", "-m", "resource constraints"],
    "conteneurs ne se voient pas": ["network", "bridge", "DNS resolution"],

    # --- Linux ---
    "service ne demarre pas": ["systemctl status", "journalctl", "failed"],
    "recharger la configuration": ["reload", "daemon-reload", "SIGHUP"],
    "ports en ecoute": ["ss", "listening", "netstat"],
    "trop de fichiers ouverts": ["nofile", "ulimit", "file descriptor"],
    "processus bloque": ["uninterruptible", "D state", "I/O wait"],
    "plus d espace disque": ["inode", "df", "disk full"],

    # --- CI/CD ---
    "partager des fichiers": ["artifacts", "upload-artifact", "download-artifact"],
    "passer des fichiers entre jobs": ["artifacts", "workflow artifacts"],
    "declencher un workflow": ["on:", "trigger", "events"],
    "plusieurs versions": ["matrix", "strategy"],
    "accelerer les builds": ["cache", "dependency caching"],
    "variables secretes": ["secrets", "encrypted secrets"],

    # --- Terraform ---
    "renommer une ressource": ["moved", "refactoring"],
    "deplacer une ressource": ["moved", "state mv"],
    "retirer du state": ["removed", "state rm"],
    "empecher la destruction": ["prevent_destroy", "lifecycle"],
    "forcer le remplacement": ["replace", "taint"],
    "ressource existante": ["import"],
    "valeur sensible": ["sensitive", "sensitive_data"],
    "exposer une valeur": ["output"],
    "boucle sur une liste": ["for_each", "count"],
    "editer le state": ["state", "purpose", "manual editing"],
    "verrouiller le state": ["state locking", "lock"],
    "state d une autre configuration": ["terraform_remote_state", "remote state data source"],
    "lire le state": ["terraform_remote_state", "state show", "state list"],
    "partager des donnees entre configurations": ["terraform_remote_state", "outputs"],
}


def _normaliser(texte: str) -> str:
    texte = unicodedata.normalize("NFD", texte.lower())
    return "".join(c for c in texte if unicodedata.category(c) != "Mn")


_GLOSSAIRE_NORM = {_normaliser(k): v for k, v in GLOSSAIRE.items()}


def termes_ajoutes(question: str) -> list[str]:
    """Termes techniques correspondant aux expressions de la question.

    La correspondance est souple : tous les mots significatifs de la clé
    doivent apparaître dans la question, pas nécessairement contigus.
    « Le disque est plein » doit matcher la clé « disque plein ».
    """
    q = _normaliser(question)
    mots_q = set(re.findall(r"[a-z0-9]+", q))

    trouves: list[str] = []
    for expression, termes in _GLOSSAIRE_NORM.items():
        mots_cle = [m for m in re.findall(r"[a-z0-9]+", expression) if len(m) > 2]
        if not mots_cle:
            continue
        # Correspondance exacte, ou tous les mots significatifs présents.
        if expression in q or all(m in mots_q for m in mots_cle):
            trouves.extend(t for t in termes if _normaliser(t) not in q)
    # Déduplication en conservant l'ordre.
    vus, uniques = set(), []
    for t in trouves:
        if t.lower() not in vus:
            vus.add(t.lower())
            uniques.append(t)
    return uniques


def etendre(question: str) -> str:
    """Question enrichie des termes du corpus, formulation d'origine gardée."""
    termes = termes_ajoutes(question)
    return f"{question} {' '.join(termes)}" if termes else question


if __name__ == "__main__":
    questions = (
        [" ".join(sys.argv[1:])]
        if len(sys.argv) > 1
        else [
            "Comment planifier une tâche récurrente dans Kubernetes ?",
            "Comment partager des fichiers entre deux jobs GitHub Actions ?",
            "Le disque est plein à cause de Docker, comment libérer de l'espace ?",
            "Un pod redémarre en boucle, que faire ?",
            "Quelle est la syntaxe du bloc moved ?",
        ]
    )
    for q in questions:
        termes = termes_ajoutes(q)
        print(f"\n  \033[1m{q}\033[0m")
        if termes:
            print(f"    \033[32m+\033[0m {', '.join(termes)}")
        else:
            print("    \033[2m(aucun terme ajouté)\033[0m")


# ─────────────────────────────────────────────────────────────────
# Garde-fou de domaine
# ─────────────────────────────────────────────────────────────────
# Le corpus élargi crée des faux positifs thématiques : une question sur
# PostgreSQL remonte le backend `pg` de Terraform, une question sur Poetry
# remonte le cache de dépendances CI. Le modèle voit un contexte plausible
# et répond — mesuré en brique 14 : les refus tombent de 5/5 à 5/10.
#
# Le prompt seul ne corrige que 1 cas sur 5. Ce garde-fou agit en amont :
# si la question nomme une technologie que le corpus ne couvre PAS, on
# refuse sans appeler le modèle.
#
# Contrairement au seuil de score abandonné en brique 10 (les populations
# se chevauchaient), ce test est lexical et déterministe.

# Technologies hors du domaine couvert. Une occurrence marginale dans le
# corpus ne compte pas : ce qui compte est qu'on ne puisse pas y répondre.
HORS_DOMAINE = {
    # Bases de données
    "postgresql", "postgres", "mysql", "mariadb", "mongodb", "redis",
    "elasticsearch", "cassandra", "oracle",
    # Gestion de configuration
    "ansible", "puppet", "chef", "saltstack", "playbook",
    # Packaging / templating K8s
    "helm", "kustomize", "chart",
    # CI hors GitHub Actions
    "jenkins", "gitlab-ci", "gitlab ci", "circleci", "travis", "bamboo",
    "teamcity", "drone",
    # Langages et gestionnaires de paquets
    "poetry", "pipenv", "npm", "yarn", "maven", "gradle", "cargo",
    "composer", "bundler",
    # Messagerie
    "kafka", "rabbitmq", "nats", "activemq",
    # Observabilité
    "prometheus", "grafana", "datadog", "splunk", "elk", "loki",
    # Virtualisation / cloud propriétaire
    "vmware", "vsphere", "esxi", "hyper-v", "openstack", "openshift",
    # Serveurs web / proxies
    "nginx.conf", "apache", "haproxy", "traefik", "envoy",
    # Systèmes
    "windows server", "iis", "powershell", "active directory",
}

# Sujets que le corpus couvre réellement : si l'un d'eux est aussi présent,
# la question est probablement légitime (« nginx dans un pod Kubernetes »).
DOMAINE_COUVERT = {
    "kubernetes", "k8s", "pod", "deployment", "statefulset", "daemonset",
    "cronjob", "configmap", "secret", "probe", "kubectl", "namespace",
    "terraform", "hcl", "state", "provider", "module", "backend",
    "docker", "conteneur", "container", "dockerfile", "image",
    "systemd", "linux", "journalctl", "systemctl",
    "github actions", "workflow", "runner",
}


def hors_domaine(question: str) -> str | None:
    """Renvoie la technologie hors domaine détectée, sinon None.

    Une question qui nomme à la fois une techno hors domaine ET un sujet
    couvert est considérée légitime : « déployer nginx sur Kubernetes »
    relève bien du corpus.
    """
    q = _normaliser(question)
    mots = set(re.findall(r"[a-z0-9.-]+", q))

    detectees = [
        techno for techno in HORS_DOMAINE
        if (techno in q if " " in techno or "." in techno else techno in mots)
    ]
    if not detectees:
        return None

    couvert = any(
        (sujet in q if " " in sujet else sujet in mots)
        for sujet in DOMAINE_COUVERT
    )
    return None if couvert else detectees[0]
