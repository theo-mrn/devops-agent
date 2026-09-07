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
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import devops_agent.core.config as config
from devops_agent.agent import tools as outils

TOURS_MAX = 12  # au-delà, l'agent boucle probablement pour rien

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

RÉPONSE
- Commence par le diagnostic, pas par le récit de tes recherches.
- Cite les observations qui le fondent (sortie de commande, fichier, doc).
- Si les faits ne suffisent pas à conclure, dis-le et indique ce qu'il
  faudrait observer de plus.
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

    # ── Boucle ───────────────────────────────────────────────────

    def demander(self, question: str) -> str:
        """Pose une question et laisse l'agent enchaîner ses outils."""
        if config.PROVIDER != "anthropic":
            raise RuntimeError(
                "La boucle d'agent exige un modèle capable d'orchestrer plusieurs "
                "outils. Un 7B local n'y arrive pas de façon fiable.\n"
                "  → RAG_PROVIDER=anthropic"
            )

        import anthropic

        client = anthropic.Anthropic(timeout=float(config.TIMEOUT_LLM))
        self.messages = [{"role": "user", "content": question}]

        self._log(f"\n\033[1m QUESTION \033[0m {question}\n")
        debut = time.time()

        for tour in range(1, self.tours_max + 1):
            reponse = client.messages.create(
                model=config.LLM_API,
                max_tokens=config.MAX_TOKENS_API,
                system=[{
                    "type": "text",
                    "text": SYSTEM,
                    # Le prompt système est identique à chaque tour : le
                    # mettre en cache fait tomber son coût à ~10 %.
                    "cache_control": {"type": "ephemeral"},
                }],
                tools=outils.DEFINITIONS,
                messages=self.messages,
            )

            self.tokens_entree += reponse.usage.input_tokens
            self.tokens_sortie += reponse.usage.output_tokens

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
                    f"{self.tokens_entree}+{self.tokens_sortie} tokens · {duree:.1f}s\033[0m"
                )
                return texte

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
