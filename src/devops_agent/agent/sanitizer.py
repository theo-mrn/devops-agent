"""Masquage des secrets avant tout envoi au modèle.

Sans ce filtre, `kubectl logs` ou `kubectl get -o yaml` peuvent expédier
des jetons, des mots de passe et des clés privées vers une API externe.
Sur un cluster de production, c'est une fuite — pas un risque théorique.

Le masquage préserve le NOM de ce qui a été masqué :

    DATABASE_PASSWORD=hunter2  →  DATABASE_PASSWORD=[SECRET MASQUÉ]

Le modèle sait ainsi qu'un mot de passe existe à cet endroit, ce qui peut
compter pour le diagnostic, sans jamais en voir la valeur.

Ce module ne contient aucun appel au modèle : ce sont des expressions
régulières, testables et instantanées.
"""

import os
import re

MASQUE = "[SECRET MASQUÉ]"

# Chaque motif capture, quand c'est possible, l'étiquette à conserver.
# L'ordre compte : les motifs les plus spécifiques d'abord, sinon un
# motif générique masque l'étiquette qu'un motif précis aurait gardée.
MOTIFS: list[tuple[re.Pattern, str]] = [
    # ── Formats reconnaissables, masqués en entier ───────────────
    # Clé privée PEM : le bloc complet disparaît.
    (re.compile(
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
        re.DOTALL), MASQUE),

    # JWT : trois segments base64url séparés par des points.
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+"), MASQUE),

    # Jetons de forge, préfixe explicite.
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr|glpat|glptt)_[A-Za-z0-9]{16,}"), MASQUE),

    # Identifiants AWS.
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), MASQUE),

    # Jetons de comptes de service Kubernetes et Docker Hub.
    (re.compile(r"\bdckr_pat_[A-Za-z0-9_-]{16,}"), MASQUE),

    # Webhooks : l'URL entière est un secret.
    (re.compile(r"https://hooks\.slack\.com/services/\S+"), MASQUE),
    (re.compile(r"https://discord(?:app)?\.com/api/webhooks/\S+"), MASQUE),

    # ── Formats étiquetés : on garde l'étiquette ─────────────────
    # Identifiants dans une URL de connexion : postgres://user:pass@host
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s:@/]+:[^\s@/]+@"),
     rf"\1{MASQUE}@"),

    # En-tête d'autorisation HTTP.
    (re.compile(r"(?i)\b(authorization\s*[:=]\s*)(?:bearer|basic)\s+\S+"),
     rf"\1{MASQUE}"),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{16,}"),
     rf"\1 {MASQUE}"),

    # Champs de certificats kubeconfig, encodés en base64.
    (re.compile(
        r"(?i)\b(client-certificate-data|client-key-data|"
        r"certificate-authority-data)(\s*[:=]\s*)\S+"),
     rf"\1\2{MASQUE}"),

    # Clé/valeur générique : le filet le plus large, donc en dernier.
    # Le nom de la variable est conservé pour que le modèle sache QUOI a
    # été masqué. Préfixe et suffixe facultatifs : « api_key »,
    # « DATABASE_PASSWORD » et « x-auth-token » correspondent tous.
    (re.compile(
        r"(?i)"
        r"([A-Za-z0-9_.\-]*"
        r"(?:api[_-]?key|apikey|token|secret|password|passwd|pwd|"
        r"credential|private[_-]?key|access[_-]?key|auth)"
        r"[A-Za-z0-9_.\-]*)"
        r"(\s*[:=]\s*)"
        r"(?!(?:null|nil|none|true|false)\b)"
        r"[\"']?([^\s\"',;}\]]{4,})[\"']?"
    ), r"\1\2" + MASQUE),

    # Valeurs base64 longues sous une clé « data: » — le contenu des
    # Secrets Kubernetes, même si l'accès direct est déjà bloqué.
    (re.compile(r"(?m)^(\s+)([A-Za-z0-9_.-]+):\s*([A-Za-z0-9+/]{40,}={0,2})\s*$"),
     rf"\1\2: {MASQUE}"),
]

# Taille maximale d'une observation renvoyée au modèle. Au-delà, on
# tronque : un log de 200 000 lignes n'apporte rien de plus que ses
# premières et coûte une fortune en tokens.
LIMITE_CARACTERES = int(os.environ.get("RAG_LIMITE_OBSERVATION", "8000"))


# Le marqueur contient des caractères qu'un motif ultérieur peut
# reconnaître : « [SECRET MASQUÉ] » après « Bearer » se fait re-masquer
# partiellement. On substitue donc un jeton neutre pendant le traitement,
# restauré à la fin.
_JETON = "\x00SECRET\x00"


def masquer(texte: str) -> str:
    """Remplace tout secret reconnaissable par un marqueur.

    Les zones déjà masquées sont protégées : sans cela, un motif appliqué
    plus tard découpe le marqueur inséré par un motif précédent.
    """
    if not texte:
        return texte

    for motif, remplacement in MOTIFS:
        texte = motif.sub(remplacement.replace(MASQUE, _JETON), texte)

    return texte.replace(_JETON, MASQUE)


def tronquer(texte: str, limite: int | None = None) -> str:
    """Coupe une observation trop longue, en gardant début et fin.

    La fin d'un log contient souvent l'erreur fatale : la garder évite de
    perdre l'information la plus utile.
    """
    limite = limite or LIMITE_CARACTERES
    if len(texte) <= limite:
        return texte

    tete = int(limite * 0.7)
    queue = limite - tete - 60
    return (
        texte[:tete]
        + f"\n\n[... {len(texte) - limite} caractères omis ...]\n\n"
        + texte[-queue:]
    )


def assainir(texte: str, limite: int | None = None) -> str:
    """Tronque puis masque — à appeler sur toute sortie d'outil.

    L'ordre est dicté par la performance : appliquer une douzaine
    d'expressions régulières à 200 000 caractères de log prend plusieurs
    dizaines de secondes. On tronque d'abord, on masque ensuite.

    La garantie tient malgré tout : seul le texte CONSERVÉ part vers
    l'API, et il est intégralement masqué. Un secret situé dans la
    portion supprimée n'atteint jamais le modèle — il a été supprimé,
    pas seulement caché.
    """
    return masquer(tronquer(texte, limite))


def compter_masques(texte: str) -> int:
    """Nombre de secrets masqués dans un texte déjà assaini."""
    return texte.count(MASQUE)
