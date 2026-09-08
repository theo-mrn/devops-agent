# Image de l'agent DevOps.
#
# Construction en deux étapes : les dépendances lourdes (PyTorch,
# sentence-transformers) restent dans l'étape de build, seul le nécessaire
# passe dans l'image finale.

FROM python:3.12-slim AS build

# uv installe les dépendances plus vite que pip et respecte le lockfile.
# Installé par son script officiel plutôt que copié depuis ghcr.io, dont
# l'accès demande une authentification dans certains environnements.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && curl -LsSf https://astral.sh/uv/install.sh | sh \
 && mv /root/.local/bin/uv /usr/local/bin/uv \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src/ src/

# Seules les dépendances de base : l'agent de diagnostic n'a pas besoin
# de PyTorch. La recherche documentaire s'ajoute avec --extra rag, au
# prix de ~2 Go et de plusieurs minutes de construction.
RUN uv sync --frozen --no-dev

# ── Image finale ─────────────────────────────────────────────────
FROM python:3.12-slim

# kubectl est requis : l'agent l'invoque plutôt que d'utiliser le client
# Python, ce qui garde les commandes lisibles dans les traces.
ARG KUBECTL_VERSION=v1.31.0
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && curl -fsSLo /usr/local/bin/kubectl \
      "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
 && chmod +x /usr/local/bin/kubectl \
 && apt-get purge -y curl \
 && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*

# Utilisateur non privilégié : l'agent n'a aucune raison d'être root.
RUN useradd --create-home --uid 10001 agent

WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY --from=build /app/src /app/src
COPY data/interne/ data/interne/
COPY eval/ eval/

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER agent

# L'index vectoriel n'est pas embarqué : il pèse 10 Mo et dépend du
# corpus. Il est construit au premier démarrage ou monté en volume.
ENTRYPOINT ["devops-agent"]
CMD ["auto"]
