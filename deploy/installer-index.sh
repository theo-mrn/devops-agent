#!/usr/bin/env bash
# Met en place l'index vectoriel : base PostgreSQL, clé de déploiement,
# CronJob d'indexation.
#
#   DEPOT=git@github.com:compte/depot.git ./deploy/installer-index.sh
#
# La seule étape manuelle est l'enregistrement de la clé publique comme
# deploy key sur la forge — le script l'affiche à la fin.

set -euo pipefail

NAMESPACE="${NAMESPACE:-devops-agent}"
DEPOT="${DEPOT:-}"
RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

vert()  { printf "\033[32m%s\033[0m\n" "$*"; }
rouge() { printf "\033[31m%s\033[0m\n" "$*"; }
gras()  { printf "\033[1m%s\033[0m\n" "$*"; }

if [[ -z "$DEPOT" ]]; then
  rouge "DEPOT n'est pas défini."
  echo "  DEPOT=git@github.com:compte/depot.git $0"
  exit 1
fi

if ! kubectl get namespace "$NAMESPACE" >/dev/null 2>&1; then
  rouge "Le namespace ${NAMESPACE} n'existe pas — lancer d'abord installer.sh"
  exit 1
fi

# ── 1. Clé de déploiement ────────────────────────────────────────

gras "1. Clé de déploiement"

if kubectl get secret agent-index-git -n "$NAMESPACE" >/dev/null 2>&1; then
  vert "   secret déjà présent"
  CLE_PUBLIQUE=""
else
  # La clé ne touche jamais le disque du dépôt : elle est créée dans un
  # répertoire temporaire, poussée dans le cluster, puis effacée.
  TEMP=$(mktemp -d)
  trap 'rm -rf "$TEMP"' EXIT

  ssh-keygen -t ed25519 -f "$TEMP/cle" -N "" -C "devops-agent-indexeur" -q
  kubectl create secret generic agent-index-git \
    --from-file=ssh-privatekey="$TEMP/cle" -n "$NAMESPACE"
  CLE_PUBLIQUE=$(cat "$TEMP/cle.pub")
  vert "   clé générée et stockée dans le cluster"
fi
echo

# ── 2. Dépôt à indexer ───────────────────────────────────────────

gras "2. Dépôt à indexer"
kubectl create configmap agent-index-config \
  --from-literal=depot="$DEPOT" \
  -n "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -
echo "   ${DEPOT}"
echo

# ── 3. Base et CronJob ───────────────────────────────────────────

gras "3. Base PostgreSQL et CronJob"

# Les identifiants de la base sont générés ici : ils ne transitent
# nulle part ailleurs.
if ! kubectl get secret agent-index-credentials -n "$NAMESPACE" >/dev/null 2>&1; then
  MOTDEPASSE=$(head -c 24 /dev/urandom | base64 | tr -d '/+=' | head -c 24)
  # CloudNativePG exige le type basic-auth pour les identifiants
  # de bootstrap.
  kubectl create secret generic agent-index-credentials \
    --type=kubernetes.io/basic-auth \
    --from-literal=username=agent \
    --from-literal=password="$MOTDEPASSE" \
    -n "$NAMESPACE"
  unset MOTDEPASSE
  vert "   identifiants de base générés"
fi

kubectl apply -f "${RACINE}/deploy/index-db.yaml"
echo

# ── 4. Attente de la base ────────────────────────────────────────

gras "4. Démarrage de la base"
if kubectl wait --for=condition=Ready cluster/agent-index \
     -n "$NAMESPACE" --timeout=300s 2>/dev/null; then
  vert "   base prête"
else
  echo "   toujours en cours — suivre avec :"
  echo "     kubectl get cluster agent-index -n ${NAMESPACE} -w"
fi
echo

# ── 5. Connexion de l'agent ──────────────────────────────────────

gras "5. Connexion de l'agent à l'index"

# CloudNativePG ne publie un secret `<cluster>-app` avec l'URI complète
# que s'il génère lui-même le mot de passe. Ici, les identifiants ont été
# fournis à l'installation : on assemble donc le DSN à partir d'eux.
UTILISATEUR=$(kubectl get secret agent-index-credentials -n "$NAMESPACE" \
                -o jsonpath='{.data.username}' | base64 -d)
MOTDEPASSE=$(kubectl get secret agent-index-credentials -n "$NAMESPACE" \
               -o jsonpath='{.data.password}' | base64 -d)
DSN="postgresql://${UTILISATEUR}:${MOTDEPASSE}@agent-index-rw.${NAMESPACE}.svc.cluster.local:5432/index"

# Le DSN vit dans son propre Secret : il ne doit pas apparaître en clair
# dans la définition du Deployment.
kubectl create secret generic agent-index-dsn \
  --from-literal=dsn="$DSN" \
  -n "$NAMESPACE" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
unset DSN MOTDEPASSE

kubectl set env deployment/devops-agent -n "$NAMESPACE" \
  RAG_DB_DSN- >/dev/null 2>&1 || true
kubectl set env deployment/devops-agent -n "$NAMESPACE" \
  --from=secret/agent-index-dsn >/dev/null
vert "   variable RAG_DB_DSN branchée sur le secret"
echo

# ── Étape manuelle ───────────────────────────────────────────────

if [[ -n "$CLE_PUBLIQUE" ]]; then
  gras "Il reste une étape manuelle"
  echo
  echo "  Enregistrer cette clé comme deploy key EN LECTURE SEULE :"
  echo
  echo "    ${CLE_PUBLIQUE}"
  echo
  echo "  Sur GitHub : Settings → Deploy keys → Add deploy key"
  echo "  Ne pas cocher « Allow write access »."
  echo
fi

gras "Ensuite"
echo "   Lancer une première indexation :"
echo "     kubectl create job --from=cronjob/agent-indexeur indexation-1 -n ${NAMESPACE}"
echo
echo "   Suivre :"
echo "     kubectl logs -f job/indexation-1 -n ${NAMESPACE}"
