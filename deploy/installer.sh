#!/usr/bin/env bash
# Installation de l'agent DevOps sur un cluster Kubernetes.
#
# Trois étapes, chacune vérifiée avant de passer à la suivante :
#   1. RBAC — namespace, ServiceAccount, ClusterRole en lecture seule
#   2. Contrôle — le compte peut-il lire, et surtout PAS écrire ?
#   3. Déploiement — image, secret, agent
#
# L'étape 2 est la raison d'être de ce script : appliquer un RBAC sans
# vérifier ce qu'il autorise revient à faire confiance à un fichier YAML.

set -euo pipefail

NAMESPACE="${NAMESPACE:-devops-agent}"
SA="${NAMESPACE}:devops-agent"
RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

vert()  { printf "\033[32m%s\033[0m\n" "$*"; }
rouge() { printf "\033[31m%s\033[0m\n" "$*"; }
gras()  { printf "\033[1m%s\033[0m\n" "$*"; }

# ── Préalables ───────────────────────────────────────────────────

command -v kubectl >/dev/null || { rouge "kubectl est absent du PATH"; exit 1; }

if ! kubectl cluster-info >/dev/null 2>&1; then
  rouge "cluster injoignable — vérifier le kubeconfig"
  exit 1
fi

gras "Cluster : $(kubectl config current-context)"
echo

# ── 1. RBAC ──────────────────────────────────────────────────────

gras "1. Application du RBAC"
kubectl apply -f "${RACINE}/deploy/rbac.yaml"
echo

# ── 2. Contrôle des permissions ──────────────────────────────────
# Un RBAC appliqué n'est pas un RBAC vérifié. On demande au serveur
# d'API ce que le compte peut faire, plutôt que de lire le YAML.

gras "2. Vérification des permissions du ServiceAccount"

erreurs=0

verifier() {
  local verbe="$1" ressource="$2" attendu="$3"
  local reponse
  reponse=$(kubectl auth can-i "$verbe" "$ressource" \
              --all-namespaces --as "system:serviceaccount:${SA}" 2>/dev/null || echo "no")

  if [[ "$reponse" == "$attendu" ]]; then
    printf "   \033[32m✓\033[0m %-34s %s\n" "$verbe $ressource" "$reponse"
  else
    printf "   \033[41m\033[97m ! \033[0m %-34s %s (attendu : %s)\n" \
           "$verbe $ressource" "$reponse" "$attendu"
    erreurs=$((erreurs + 1))
  fi
}

# Ce que l'agent doit pouvoir faire.
for action in "get pods" "list pods" "watch pods" "get pods/log" \
              "get services" "get endpoints" "get deployments" \
              "get persistentvolumeclaims" "get nodes" "get events"; do
  verifier ${action} "yes"
done

echo
# Ce qu'il ne doit surtout pas pouvoir faire.
for action in "delete pods" "create pods" "patch deployments" \
              "delete nodes" "get secrets" "create secrets" \
              "create pods/exec" "patch nodes"; do
  verifier ${action} "no"
done

echo
if (( erreurs > 0 )); then
  rouge "${erreurs} permission(s) incorrecte(s) — installation interrompue."
  rouge "Le RBAC ne correspond pas à l'intention de lecture seule."
  exit 1
fi
vert "Permissions conformes : lecture seule stricte, aucun accès aux Secrets."
echo

# ── 3. Clé API ───────────────────────────────────────────────────

gras "3. Clé API"

if kubectl get secret devops-agent-api -n "$NAMESPACE" >/dev/null 2>&1; then
  vert "   secret déjà présent"
else
  cle="${ANTHROPIC_API_KEY:-}"
  if [[ -z "$cle" && -f "${RACINE}/.env" ]]; then
    cle=$(grep -E '^ANTHROPIC_API_KEY=' "${RACINE}/.env" | cut -d= -f2- | tr -d '"'"'"' ' || true)
  fi

  if [[ -z "$cle" ]]; then
    rouge "   aucune clé trouvée."
    echo "   Définir ANTHROPIC_API_KEY, ou créer le secret à la main :"
    echo "     kubectl create secret generic devops-agent-api \\"
    echo "       --from-literal=anthropic-api-key=sk-ant-... -n ${NAMESPACE}"
    exit 1
  fi

  kubectl create secret generic devops-agent-api \
    --from-literal=anthropic-api-key="$cle" -n "$NAMESPACE"
  vert "   secret créé"
fi
echo

# ── 4. Déploiement ───────────────────────────────────────────────

gras "4. Déploiement de l'agent"

# Le Secret est créé à l'étape 3, hors du manifest : rien ici ne peut
# écraser la clé.
kubectl apply -f "${RACINE}/deploy/agent.yaml"

echo
gras "Installation terminée."
echo
echo "   Suivre le démarrage :"
echo "     kubectl logs -f deploy/devops-agent -n ${NAMESPACE}"
echo
echo "   Vérifier les permissions à tout moment :"
echo "     devops-agent audit --sa ${SA}"
