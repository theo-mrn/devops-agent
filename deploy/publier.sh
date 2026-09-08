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
COMPTE="${COMPTE:-$(gh api user --jq .login 2>/dev/null || echo "")}"
REGISTRE="${REGISTRE:-ghcr.io/${COMPTE}}"

if [[ -z "$COMPTE" && "$REGISTRE" == "ghcr.io/" ]]; then
  printf "\033[31mCompte introuvable — définir COMPTE ou REGISTRE.\033[0m\n"
  exit 1
fi

IMAGE="${REGISTRE}/devops-agent"

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
echo "Sur GHCR, une image est privée par défaut. Pour la rendre publique :"
echo "  https://github.com/users/${COMPTE}/packages/container/devops-agent/settings"
echo
echo "Sinon, créer un imagePullSecret dans le cluster :"
echo "  kubectl create secret docker-registry ghcr \\"
echo "    --docker-server=ghcr.io --docker-username=${COMPTE} \\"
echo "    --docker-password=\$(gh auth token) -n devops-agent"
