"""Vérification mécanique des affirmations d'un diagnostic.

Un modèle peut affirmer « la limite mémoire est à 512Mi » alors qu'elle
est à 2Gi. Cette erreur-là — factuelle — est le risque principal d'un
agent de diagnostic, et elle est vérifiable sans aucun appel au modèle :
il suffit de relire le cluster.

Ce module extrait les affirmations vérifiables du rapport, les confronte
au cluster, et signale les contradictions.

    « limite mémoire : 512Mi »  →  kubectl get pod -o jsonpath  →  2Gi
    → CONTRADICTION signalée dans le rapport

C'est complémentaire d'un confirmateur LLM, qui attrape les erreurs de
raisonnement : ici on attrape les erreurs de fait, gratuitement.
"""

import json
import re
import subprocess
from dataclasses import dataclass

from devops_agent.agent import tools


@dataclass
class Affirmation:
    """Une affirmation extraite du diagnostic, avec son verdict."""
    sujet: str          # ce qui est affirmé : « limite mémoire »
    annonce: str        # la valeur affirmée par le modèle
    reelle: str | None  # la valeur lue sur le cluster
    verdict: str        # "confirme", "contredit", "constat", "invérifiable"

    def __str__(self) -> str:
        if self.verdict == "confirme":
            return f"✓ {self.sujet} : {self.annonce}"
        if self.verdict == "contredit":
            return f"✗ {self.sujet} : annoncé {self.annonce}, réel {self.reelle}"
        if self.verdict == "constat":
            # Un fait établi par le contrôle lui-même, pas une
            # confrontation avec le rapport.
            return f"! {self.sujet} : {self.annonce}"
        return f"? {self.sujet} : {self.annonce} (non vérifiable)"


def _normaliser_quantite(valeur: str) -> float | None:
    """Convertit une quantité Kubernetes en octets ou en millicores.

    « 512Mi », « 2Gi » et « 2048Mi » doivent être comparables : le modèle
    peut employer une unité différente de celle du manifest sans se
    tromper pour autant.
    """
    valeur = valeur.strip()
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([A-Za-z]*)", valeur)
    if not m:
        return None

    nombre, unite = float(m.group(1)), m.group(2)
    facteurs = {
        "": 1, "m": 0.001,                       # CPU
        "Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4,
        "K": 1000, "M": 1000**2, "G": 1000**3, "T": 1000**4,
        "k": 1000,
    }
    facteur = facteurs.get(unite)
    return nombre * facteur if facteur is not None else None


def _equivalentes(a: str, b: str) -> bool:
    """Deux quantités désignent-elles la même valeur ?"""
    if a.strip().lower() == b.strip().lower():
        return True
    na, nb = _normaliser_quantite(a), _normaliser_quantite(b)
    return na is not None and nb is not None and abs(na - nb) < 1e-6


def _valeur_cluster(chemin: str, kind: str, nom: str, namespace: str) -> str | None:
    """Lit une valeur précise sur le cluster, via jsonpath."""
    commande = f"get {kind} {nom} -n {namespace} -o jsonpath={{{chemin}}}"
    if tools.verifier_commande(commande) is not None:
        return None
    try:
        r = subprocess.run(
            ["kubectl", *commande.split()],
            capture_output=True, text=True, timeout=15,
        )
    except subprocess.TimeoutExpired:
        return None
    return r.stdout.strip() or None if r.returncode == 0 else None


# ── Extraction des affirmations ──────────────────────────────────
# Chaque motif capture une affirmation vérifiable et le chemin jsonpath
# qui permet de la contrôler.

MOTIFS_MEMOIRE = [
    re.compile(r"(?i)limit(?:e|s)?\s+(?:de\s+)?m[ée]moire\s*(?:est\s*)?"
               r"(?:à|:|=|de)?\s*[«\"'`]?(\d+(?:\.\d+)?\s*[KMGT]i?)[»\"'`]?"),
    re.compile(r"(?i)`?(?:resources\.)?limits\.memory`?\s*[:=]\s*"
               r"[«\"'`]?(\d+(?:\.\d+)?\s*[KMGT]i?)[»\"'`]?"),
    re.compile(r"(?i)memory\s*:\s*[«\"'`]?(\d+(?:\.\d+)?[KMGT]i)[»\"'`]?"),
]

MOTIFS_REPLICAS = [
    re.compile(r"(?i)(\d+)\s+replicas?\s+(?:configur|voulu|d[ée]sir|attendu)"),
    re.compile(r"(?i)replicas?\s*[:=]\s*(\d+)"),
]

MOTIFS_IMAGE = [
    re.compile(r"(?i)image\s*[:=]\s*[«\"'`]?([a-z0-9][\w.\-/]*:[\w.\-]+)[»\"'`]?"),
]


def _premiere_capture(motifs: list[re.Pattern], texte: str) -> str | None:
    for motif in motifs:
        m = motif.search(texte)
        if m:
            return m.group(1)
    return None


def verifier(rapport: str, ressource: str, nom: str,
             namespace: str) -> list[Affirmation]:
    """Confronte les affirmations du rapport à l'état réel du cluster.

    Seules les ressources dont le chemin jsonpath est connu sont
    vérifiables : un rapport sur un nœud ou un PVC ne produira aucune
    affirmation contrôlable ici, ce qui est correct — mieux vaut ne rien
    dire que de contredire à tort.
    """
    affirmations: list[Affirmation] = []

    if ressource in ("pod", "pods"):
        annonce = _premiere_capture(MOTIFS_MEMOIRE, rapport)
        if annonce:
            reelle = _valeur_cluster(
                ".spec.containers[0].resources.limits.memory",
                "pod", nom, namespace,
            )
            affirmations.append(Affirmation(
                sujet="limite mémoire", annonce=annonce, reelle=reelle,
                verdict=("invérifiable" if reelle is None
                         else "confirme" if _equivalentes(annonce, reelle)
                         else "contredit"),
            ))

        annonce = _premiere_capture(MOTIFS_IMAGE, rapport)
        if annonce:
            reelle = _valeur_cluster(
                ".spec.containers[0].image", "pod", nom, namespace
            )
            affirmations.append(Affirmation(
                sujet="image", annonce=annonce, reelle=reelle,
                verdict=("invérifiable" if reelle is None
                         else "confirme" if annonce == reelle
                         else "contredit"),
            ))

    elif ressource in ("deployment", "deployments"):
        annonce = _premiere_capture(MOTIFS_REPLICAS, rapport)
        if annonce:
            reelle = _valeur_cluster(".spec.replicas", "deployment",
                                     nom, namespace)
            affirmations.append(Affirmation(
                sujet="replicas configurés", annonce=annonce, reelle=reelle,
                verdict=("invérifiable" if reelle is None
                         else "confirme" if annonce == reelle
                         else "contredit"),
            ))

    return affirmations


def verifier_selecteur(service: str, namespace: str) -> Affirmation | None:
    """Vérifie mécaniquement si un sélecteur de Service correspond à des pods.

    C'est la cause habituelle d'un service sans endpoint, et elle se
    tranche sans le moindre raisonnement : on lit le sélecteur, on compte
    les pods qui portent ces labels.
    """
    selecteur = _valeur_cluster(".spec.selector", "service", service, namespace)
    if not selecteur:
        return None

    # jsonpath rend le sélecteur en JSON : {"app":"web","tier":"front"}.
    try:
        paires = json.loads(selecteur)
    except json.JSONDecodeError:
        # Repli sur le format map[clé:valeur] de certaines versions.
        paires = dict(re.findall(r"([\w./-]+):([^\s\]]+)", selecteur))
    if not paires:
        return None

    labels = ",".join(f"{cle}={valeur}" for cle, valeur in paires.items())
    commande = f"get pods -n {namespace} -l {labels} -o name"
    if tools.verifier_commande(commande) is not None:
        return None

    try:
        r = subprocess.run(["kubectl", *commande.split()],
                           capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        return None

    correspondants = len([l for l in r.stdout.splitlines() if l.strip()])
    if correspondants == 0:
        return Affirmation(
            sujet="sélecteur du service",
            annonce=f"aucun pod ne porte « {labels} » — cause probable "
                    "du service sans endpoint",
            reelle="0",
            verdict="constat",
        )
    return Affirmation(
        sujet=f"sélecteur « {labels} »",
        annonce=f"{correspondants} pod(s) correspondant(s)",
        reelle=str(correspondants),
        verdict="confirme",
    )


def formater(affirmations: list[Affirmation]) -> str:
    """Rend le bloc de vérification à ajouter au rapport."""
    if not affirmations:
        return ""

    lignes = ["", "## Vérification mécanique", ""]
    lignes += [f"  {a}" for a in affirmations]

    contredites = [a for a in affirmations if a.verdict == "contredit"]
    if contredites:
        lignes += [
            "",
            f"  ⚠ {len(contredites)} affirmation(s) du rapport contredite(s) "
            "par l'état réel du cluster — relire le diagnostic avant "
            "d'appliquer le correctif.",
        ]
    return "\n".join(lignes)
