#!/usr/bin/env bash
# Construit et publie l'image sur un registre.
#
#   ./deploy/publier.sh                 → ghcr.io/<compte gh>/devops-agent:<version>
#   REGISTRE=docker.io/moncompte ./deploy/publier.sh
#
# La version est lue depuis pyproject.toml : une image est ainsi toujours
# rattachée à un état précis du code.

set -euo pipefail

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

VERSION=$(grep -m1 '^version' "${RACINE}/pyproject.toml" | cut -d'"' -f2)
# Docker Hub par défaut : les images y sont publiques d'emblée, là où
# GHCR les crée privées même depuis un dépôt public.
#
#   COMPTE=mon-compte ./deploy/publier.sh
COMPTE="${COMPTE:-${DOCKERHUB_USER:-maxwellfaraday}}"
REGISTRE="${REGISTRE:-docker.io/${COMPTE}}"

IMAGE="${REGISTRE}/devops-agent"

# ── Vérification de l'accès au registre ──────────────────────────
# Un « denied: denied » après trois minutes de construction est une
# perte de temps : on vérifie l'authentification d'abord.

if ! docker system info 2>/dev/null | grep -qi username \
   && ! grep -q "docker.io" ~/.docker/config.json 2>/dev/null; then
  printf "\033[31mNon authentifié sur le registre.\033[0m\n"
  echo "    docker login"
  exit 1
fi

printf "\033[1mConstruction\033[0m %s:%s\n" "$IMAGE" "$VERSION"

# Les nœuds k3s sont en amd64 ; une image construite sur Mac serait en
# arm64 et refuserait de démarrer. On force donc la plateforme cible.
PLATEFORME="${PLATEFORME:-linux/amd64}"

docker buildx build \
  --platform "$PLATEFORME" \
  -t "${IMAGE}:${VERSION}" \
  -t "${IMAGE}:latest" \
  --push \
  "$RACINE"

printf "\033[32mPubliée :\033[0m %s:%s\n\n" "$IMAGE" "$VERSION"
echo "Déployer :"
echo "  kubectl set image deployment/devops-agent \\"
echo "    agent=${IMAGE}:${VERSION} -n devops-agent"
