"""Point d'entrée en ligne de commande.

    devops-agent ask "ma question"        interroge la documentation (RAG)
    devops-agent diagnose "..."           lance l'agent outillé
    devops-agent index                    reconstruit l'index vectoriel
    devops-agent fetch                    télécharge les sources
    devops-agent eval [--fast] [--cat X]  mesure la qualité
    devops-agent config                   affiche la configuration active
"""

import argparse
import sys


def main() -> int:
    parseur = argparse.ArgumentParser(
        prog="devops-agent",
        description="Agent DevOps : RAG et diagnostic d'infrastructure.",
    )
    sous = parseur.add_subparsers(dest="commande", metavar="COMMANDE")

    p = sous.add_parser("ask", help="interroge la documentation indexée")
    p.add_argument("question", nargs="+")

    p = sous.add_parser("diagnose", help="lance l'agent outillé (API requise)")
    p.add_argument("question", nargs="+")
    p.add_argument("--tours", type=int, default=12, help="limite de tours")
    p.add_argument("--modele", help="modèle API, ex. claude-sonnet-5")
    p.add_argument("--budget", type=float, help="plafond de dépense en dollars")

    p = sous.add_parser("watch", help="surveille le cluster par événements")
    p.add_argument("--diagnose", action="store_true",
                   help="déclenche l'agent sur chaque anomalie (coûte de l'API)")
    p.add_argument("--duree", type=int, help="arrêt automatique après N secondes")

    p = sous.add_parser("auto", help="surveille ET diagnostique en autonomie")
    p.add_argument("--budget-jour", type=float, default=None,
                   help="plafond de dépense sur 24 h (défaut 5 $)")
    p.add_argument("--modele", help="modèle API, ex. claude-sonnet-5")
    p.add_argument("--dry-run", action="store_true",
                   help="montre les diagnostics qui seraient lancés, sans dépenser")
    p.add_argument("--duree", type=int, help="arrêt automatique après N secondes")

    sous.add_parser("index", help="reconstruit l'index vectoriel")
    sous.add_parser("fetch", help="télécharge les sources documentaires")
    sous.add_parser("config", help="affiche la configuration active")
    sous.add_parser("audit", help="vérifie les permissions réelles sur le cluster")

    p = sous.add_parser("eval", help="mesure la qualité du pipeline")
    p.add_argument("--fast", action="store_true", help="retrieval seul, sans LLM")
    p.add_argument("--cat", help="limite à une catégorie")

    args = parseur.parse_args()
    if not args.commande:
        parseur.print_help()
        return 1

    if args.commande == "ask":
        from devops_agent.retrieval import pipeline
        pipeline.repondre(" ".join(args.question))

    elif args.commande == "diagnose":
        from devops_agent.agent import loop
        from devops_agent.core import config

        if args.modele:
            config.LLM_API = args.modele
        if args.budget:
            loop.BUDGET_MAX = args.budget

        agent = loop.Agent(tours_max=args.tours)
        print(f"\n{agent.demander(' '.join(args.question))}\n")

    elif args.commande == "auto":
        from devops_agent.agent.autonome import Autonome, BUDGET_JOUR

        Autonome(
            budget_jour=args.budget_jour or BUDGET_JOUR,
            dry_run=args.dry_run,
            modele=args.modele,
        ).demarrer(duree_max=args.duree)

    elif args.commande == "watch":
        from devops_agent.agent.watcher import Surveillance

        rappel = None
        if args.diagnose:
            from devops_agent.agent.loop import Agent

            def rappel(evenement):
                print(f"\n\033[1m  DIAGNOSTIC AUTOMATIQUE \033[0m {evenement}\n")
                agent = Agent()
                question = (
                    f"Le pod {evenement.namespace}/{evenement.pod} est en état "
                    f"{evenement.etat} ({evenement.redemarrages} redémarrages). "
                    "Diagnostique la cause et propose une correction."
                )
                print(agent.demander(question))
                print()

        Surveillance().suivre(sur_evenement=rappel, duree_max=args.duree)

    elif args.commande == "index":
        from devops_agent.ingestion import index
        index.main()

    elif args.commande == "fetch":
        from devops_agent.ingestion import sources
        sources.main()

    elif args.commande == "config":
        from devops_agent.core import config
        print(f"\n\033[1m CONFIGURATION \033[0m\n")
        print(config.resume())
        print()

    elif args.commande == "audit":
        from devops_agent.agent import tools

        print(f"\n\033[1m PERMISSIONS \033[0m {tools.contexte_kubernetes()}\n")
        resultats = tools.auditer_permissions()
        if not resultats:
            print("  cluster injoignable — impossible de vérifier\n")
            return 1

        problemes = 0
        for action, autorise, attendu in resultats:
            if autorise == attendu:
                marque = "\033[32m✓\033[0m"
            else:
                marque = "\033[41m\033[97m ! \033[0m"
                problemes += 1
            etat = "autorisé" if autorise else "refusé"
            print(f"  {marque} {action:<32} {etat}")

        print()
        if problemes:
            print(f"  \033[31m{problemes} permission(s) inattendue(s) — "
                  f"appliquer deploy/rbac.yaml\033[0m\n")
            return 1
        print("  \033[32mLes permissions correspondent au RBAC attendu.\033[0m\n")

    elif args.commande == "eval":
        from devops_agent.evaluation import runner
        argv = ["runner"]
        if args.fast:
            argv.append("--retrieval-seul")
        if args.cat:
            argv += ["--categorie", args.cat]
        sys.argv = argv
        runner.main()

    return 0


if __name__ == "__main__":
    sys.exit(main())
