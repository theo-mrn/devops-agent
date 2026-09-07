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

    sous.add_parser("index", help="reconstruit l'index vectoriel")
    sous.add_parser("fetch", help="télécharge les sources documentaires")
    sous.add_parser("config", help="affiche la configuration active")

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
        from devops_agent.agent.loop import Agent
        agent = Agent(tours_max=args.tours)
        print(f"\n{agent.demander(' '.join(args.question))}\n")

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
