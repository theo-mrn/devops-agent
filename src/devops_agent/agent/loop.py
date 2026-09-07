"""La boucle d'agent : le modèle décide, ce code exécute.

Le mécanisme, en quatre temps :

    1. on envoie au modèle la question + la liste des outils disponibles ;
    2. il répond soit du texte (c'est fini), soit « appelle tel outil » ;
    3. ce code exécute l'outil et renvoie le résultat au modèle ;
    4. retour en 1, jusqu'à une réponse textuelle ou la limite de tours.

Le modèle ne fait JAMAIS rien lui-même : il demande, ce code décide s'il
obéit. C'est ce qui rend la sûreté possible — les refus vivent dans
`outils.py`, pas dans le prompt.

Usage :
    uv run python src/agent/boucle.py "un pod crashe en prod, pourquoi ?"
"""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import devops_agent.core.config as config
from devops_agent.agent import overview
from devops_agent.agent import tools as outils

TOURS_MAX = 12  # au-delà, l'agent boucle probablement pour rien

# Prix en $ par million de tokens : (entrée, sortie, lecture cache,
# écriture cache). Une écriture coûte 125 % d'une entrée normale ; elle
# est rentabilisée dès la deuxième lecture.
TARIFS = {
    "claude-haiku-4-5": (1.00, 5.00, 0.10, 1.25),
    "claude-sonnet-5":  (2.00, 10.00, 0.20, 2.50),
    "claude-opus-5":    (5.00, 25.00, 0.50, 6.25),
    "claude-opus-4-8":  (5.00, 25.00, 0.50, 6.25),
}

# Plafond de dépense par diagnostic. Au-delà, l'agent s'arrête et rend ce
# qu'il a — un garde-fou contre la boucle coûteuse.
BUDGET_MAX = float(os.environ.get("RAG_BUDGET_MAX", "0.50"))

SYSTEM = """Tu es un ingénieur SRE/DevOps expert, chargé de diagnostiquer une
infrastructure réelle.

MÉTHODE
1. Établis les faits avant d'expliquer. Utilise tes outils pour observer
   l'état réel plutôt que de supposer.
2. Enchaîne les observations : un symptôme mène à une commande, dont le
   résultat mène à la suivante.
3. Consulte la documentation quand tu as besoin d'une procédure ou d'une
   syntaxe, pas pour ce que tu observes directement.

CONTRAINTES
- Tes outils sont en LECTURE SEULE. Tu ne peux rien modifier, et c'est
  volontaire.
- Si tu proposes une correction, présente-la comme une recommandation à
  valider par un humain, jamais comme une action effectuée.
- Ne propose une commande destructive qu'en dernier recours, après avoir
  épuisé l'investigation, et en explicitant ses conséquences.

FORMAT DE RÉPONSE

Structure ta réponse en quatre parties, dans cet ordre :

## Diagnostic

Une à trois phrases : ce qui se passe et pourquoi. Pas de récit de tes
recherches.

## Constats

Les observations qui fondent le diagnostic, une par ligne, avec leur
source. Cite les valeurs réelles que tu as lues, pas des généralités.
Exemple :
  - `resources.limits.memory: 512Mi` (manifest, ligne 34)
  - consommation observée : 498Mi juste avant le kill (kubectl top)
  - `Last State: Terminated / Reason: OOMKilled` (kubectl describe)

## Correctif

La ou les commandes EXACTES à exécuter, dans un bloc de code, prêtes à
copier. Remplace tous les paramètres par leurs valeurs réelles — jamais
de `<pod>` ni de `<namespace>` : tu les connais.

Quand la correction porte sur un manifest, donne le patch exact :

```bash
kubectl patch deployment payment-svc -n prod --type=json \
  -p='[{"op":"replace","path":"/spec/template/spec/containers/0/resources/limits/memory","value":"1Gi"}]'
```

ou le fragment YAML à modifier, en indiquant le fichier et le chemin.

Si plusieurs corrections sont possibles, ordonne-les de la moins à la
plus intrusive, et dis laquelle tu recommandes.

## Vérification

La commande qui permet de confirmer que le correctif a fonctionné.

RÈGLES SUR LE CORRECTIF
- Tu n'appliques RIEN toi-même : tes outils sont en lecture seule. Tu
  écris ce qu'un humain exécutera après relecture.
- Une commande destructive n'apparaît qu'en dernier recours, précédée de
  ses conséquences explicites.
- Si les faits ne suffisent pas à conclure, dis-le franchement et indique
  quelle observation manque, plutôt que de proposer un correctif au
  jugé.
"""


class Agent:
    """Un agent outillé. Chaque instance porte sa propre conversation."""

    def __init__(self, verbeux: bool = True, tours_max: int = TOURS_MAX):
        self.verbeux = verbeux
        self.tours_max = tours_max
        self.messages: list[dict] = []
        self.trace: list[dict] = []  # historique des outils appelés
        self.tokens_entree = 0
        self.tokens_sortie = 0
        self.tokens_cache = 0            # lus depuis le cache (~10 % du prix)
        self.tokens_ecriture_cache = 0   # écrits dans le cache (~125 %)

    # ── Coût ─────────────────────────────────────────────────────

    def cout(self) -> float:
        """Coût de la conversation en dollars."""
        tarif = TARIFS.get(config.LLM_API)
        if tarif is None:
            return 0.0
        pe, ps, pc, pw = tarif
        return (
            self.tokens_entree / 1e6 * pe
            + self.tokens_sortie / 1e6 * ps
            + self.tokens_cache / 1e6 * pc
            + self.tokens_ecriture_cache / 1e6 * pw
        )

    def resume_cout(self) -> str:
        economie = ""
        total_lu = self.tokens_entree + self.tokens_cache
        if total_lu:
            part = self.tokens_cache / total_lu * 100
            economie = f" · {part:.0f}% depuis le cache"
        return (
            f"{self.tokens_entree} frais + {self.tokens_cache} cache "
            f"+ {self.tokens_sortie} générés{economie} · "
            f"\033[1m{self.cout():.4f} $\033[0m"
        )

    # ── Affichage ────────────────────────────────────────────────

    def _log(self, texte: str) -> None:
        if self.verbeux:
            print(texte, flush=True)

    def _log_appel(self, tour: int, nom: str, args: dict) -> None:
        detail = ", ".join(f"{k}={v!r}" for k, v in args.items())
        self._log(f"  \033[2m[{tour}]\033[0m \033[36m{nom}\033[0m({detail[:100]})")

    def _log_resultat(self, resultat: str) -> None:
        premiere = resultat.strip().splitlines()[0] if resultat.strip() else "(vide)"
        marque = "\033[31m" if resultat.startswith(("[refusé]", "[erreur]")) else "\033[2m"
        self._log(f"      {marque}→ {premiere[:90]}\033[0m")

    @staticmethod
    def _avec_cache(messages: list[dict]) -> list[dict]:
        """Pose un point de cache sur le dernier message.

        Le cache Anthropic fonctionne par préfixe : marquer le dernier
        bloc rend cachable tout ce qui précède. Sans cela, l'historique
        entier est refacturé plein tarif à chaque tour.

        Les messages ne sont pas modifiés en place : `self.messages` doit
        rester propre pour les tours suivants.
        """
        if not messages:
            return messages

        copie = list(messages)
        dernier = copie[-1]
        contenu = dernier["content"]

        # Un contenu textuel simple devient un bloc annoté.
        if isinstance(contenu, str):
            copie[-1] = {
                "role": dernier["role"],
                "content": [{
                    "type": "text", "text": contenu,
                    "cache_control": {"type": "ephemeral"},
                }],
            }
            return copie

        # Une liste de blocs : on annote le dernier. Les blocs venus de
        # l'API sont des objets Pydantic, à convertir en dict.
        blocs = [b if isinstance(b, dict) else b.model_dump() for b in contenu]
        if blocs:
            blocs[-1] = {**blocs[-1], "cache_control": {"type": "ephemeral"}}
        copie[-1] = {"role": dernier["role"], "content": blocs}
        return copie

    # ── Boucle ───────────────────────────────────────────────────

    def demander(self, question: str, avec_apercu: bool = True) -> str:
        """Pose une question et laisse l'agent enchaîner ses outils.

        `avec_apercu` injecte l'état du cluster dans la première question.
        Sans lui, l'agent gaspille trois ou quatre tours à découvrir ce
        qui existe avant d'attaquer le problème posé.
        """
        # La boucle exige un modèle capable d'orchestrer plusieurs outils :
        # un 7B local n'y arrive pas de façon fiable. Dès qu'une clé est
        # disponible, on bascule sans que l'utilisateur ait à le demander.
        if config.PROVIDER != "anthropic":
            if not config.cle_api_disponible():
                raise RuntimeError(
                    "Le diagnostic outillé demande un modèle capable d'orchestrer "
                    "plusieurs outils.\n"
                    "  → renseigner ANTHROPIC_API_KEY dans .env"
                )
            config.PROVIDER = "anthropic"
            self._log(f"\033[2m  bascule sur {config.LLM_API}\033[0m")

        import anthropic

        client = anthropic.Anthropic(timeout=float(config.TIMEOUT_LLM))

        contenu = question
        if avec_apercu:
            etat = overview.apercu()
            # Un cluster injoignable ne doit pas bloquer l'agent : il lui
            # reste la documentation et les fichiers.
            if not etat.startswith("["):
                contenu = (
                    f"ÉTAT ACTUEL DU CLUSTER\n\n{etat}\n\n"
                    f"---\n\nQUESTION : {question}"
                )
                self._log(f"\033[2m  aperçu injecté ({len(etat)} caractères)\033[0m")
            else:
                self._log(f"\033[33m  {etat}\033[0m")

        self.messages = [{"role": "user", "content": contenu}]
        self._log(f"\n\033[1m QUESTION \033[0m {question}\n")
        debut = time.time()

        for tour in range(1, self.tours_max + 1):
            # Le coût d'un agent vient de la RÉPÉTITION : chaque tour
            # renvoie tout l'historique, donc un `describe` de 1800 tokens
            # obtenu au tour 1 est refacturé à chaque tour suivant.
            #
            # Le cache s'applique par PRÉFIXE : en posant un point de
            # cache sur le dernier message, tout ce qui précède (système,
            # outils, historique complet) est lu à ~10 % du prix.
            messages = self._avec_cache(self.messages)

            reponse = client.messages.create(
                model=config.LLM_API,
                max_tokens=config.MAX_TOKENS_API,
                system=[{
                    "type": "text",
                    "text": SYSTEM,
                    "cache_control": {"type": "ephemeral"},
                }],
                tools=outils.DEFINITIONS,
                messages=messages,
            )

            self.tokens_entree += reponse.usage.input_tokens
            self.tokens_sortie += reponse.usage.output_tokens
            self.tokens_cache += getattr(reponse.usage, "cache_read_input_tokens", 0) or 0
            self.tokens_ecriture_cache += (
                getattr(reponse.usage, "cache_creation_input_tokens", 0) or 0
            )

            # Un refus de sécurité laisse `content` vide : le traiter avant
            # d'itérer sur les blocs.
            if reponse.stop_reason == "refusal":
                motif = getattr(reponse.stop_details, "category", "non précisé")
                return f"[le modèle a refusé de répondre : {motif}]"

            # L'historique doit recevoir les blocs tels quels, pas le texte
            # extrait : les blocs tool_use portent des identifiants que la
            # requête suivante doit référencer.
            self.messages.append({"role": "assistant", "content": reponse.content})

            if reponse.stop_reason != "tool_use":
                texte = "".join(b.text for b in reponse.content if b.type == "text")
                duree = time.time() - debut
                self._log(
                    f"\n\033[2m→ {tour} tour(s) · {len(self.trace)} appel(s) d'outil · "
                    f"{duree:.1f}s\033[0m"
                )
                self._log(f"\033[2m  {self.resume_cout()}\033[0m")
                return texte

            # Garde-fou budgétaire : une boucle qui s'emballe coûte cher.
            if self.cout() > BUDGET_MAX:
                self._log(f"\n\033[33m  budget de {BUDGET_MAX} $ atteint\033[0m")
                return (
                    f"[budget de {BUDGET_MAX} $ atteint après {tour} tours. "
                    f"Éléments recueillis : "
                    f"{', '.join(a['outil'] for a in self.trace)}. "
                    "Reformuler la question plus précisément, ou relever "
                    "RAG_BUDGET_MAX.]"
                )

            # Le modèle peut demander PLUSIEURS outils dans un même tour.
            # Tous les résultats doivent repartir dans UN SEUL message
            # utilisateur, sinon le modèle cesse de paralléliser.
            resultats = []
            for bloc in reponse.content:
                if bloc.type != "tool_use":
                    continue

                self._log_appel(tour, bloc.name, bloc.input)
                sortie = outils.executer(bloc.name, bloc.input)
                self._log_resultat(sortie)

                self.trace.append({
                    "tour": tour, "outil": bloc.name,
                    "arguments": bloc.input, "resultat": sortie[:400],
                })
                resultats.append({
                    "type": "tool_result",
                    "tool_use_id": bloc.id,
                    "content": sortie,
                })

            self.messages.append({"role": "user", "content": resultats})

        return (
            f"[limite de {self.tours_max} tours atteinte sans conclusion — "
            "la question est peut-être trop large, ou un outil renvoie "
            "systématiquement une erreur]"
        )


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        return

    agent = Agent()
    reponse = agent.demander(" ".join(sys.argv[1:]))
    print(f"\n{reponse}\n")


if __name__ == "__main__":
    main()
