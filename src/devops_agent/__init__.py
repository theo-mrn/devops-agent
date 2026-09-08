"""Agent DevOps : RAG et diagnostic d'infrastructure.

Un assistant qui répond sur Kubernetes, Terraform, Docker, Linux et CI/CD
en citant ses sources, et qui peut observer un cluster réel en lecture
seule pour établir un diagnostic.
"""

# Lue depuis les métadonnées du paquet : une seule source de vérité,
# pyproject.toml. Un numéro écrit en double finit toujours par diverger.
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("devops-agent")
except PackageNotFoundError:       # exécution depuis les sources
    __version__ = "0.0.0+dev"
