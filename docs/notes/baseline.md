# Baseline — modèle nu, sans RAG

Modèle : `qwen2.5-coder:7b-instruct-q4_K_M` · M2 Pro 16 Go · ~29 tok/s

## Q1 — Pod en CrashLoopBackOff, exit code 137

**Verdict : correcte sur le fond, imprécise sur la méthode.**

- Identifie bien SIGKILL et la piste mémoire.
- Dilue avec du remplissage : « problème de configuration ou erreur dans le code ».
- ⚠️ Propose `kubectl logs <pod>` **sans `--previous`** : inutile après un
  redémarrage, on lit les logs de la nouvelle instance et pas de celle qui a crashé.
- Ne mentionne jamais `Last State: Terminated / Reason: OOMKilled`, qui est
  pourtant la preuve à chercher.

→ Corrigeable par **fine-tuning** (rigueur de la démarche de diagnostic).

## Q2 — Manifest Deployment nginx

**Verdict : bon.** YAML syntaxiquement valide, limits + requests + readinessProbe
correctement structurés.

- ⚠️ `image: nginx:latest` — mauvaise pratique, déploiement non reproductible.

→ Corrigeable par **fine-tuning** (conventions internes).

## Q3 — Bloc `moved` en Terraform

**Verdict : HALLUCINATION. Le cas d'école.**

- Annonce « introduit dans Terraform **0.12.13** ».
  **FAUX** — la vraie réponse est **Terraform 1.1** (décembre 2021).
- Fausse précision caractéristique : pas « 0.12 » mais « 0.12.13 ».
  Le modèle génère ce qui *ressemble* à un numéro de version.
- N'a **pas respecté** la consigne système « dis-le si tu n'es pas certain ».
  Ton parfaitement assuré.
- Syntaxe partiellement fausse : écrit `from = "ancien_chemin"` avec des
  guillemets, alors que `from`/`to` attendent des **références** non quotées
  (`from = aws_instance.foo`).

→ **Non corrigeable par fine-tuning.** C'est structurel : un LLM ne sait pas
qu'il ne sait pas. C'est la raison d'être du **RAG**.

## Conclusion

| Question | Défaut | Remède |
|---|---|---|
| Q1 | Méthode imprécise | Fine-tuning |
| Q2 | Convention discutable | Fine-tuning |
| Q3 | **Fait inventé** | **RAG** |

La séparation annoncée dans le README est validée empiriquement :
le RAG apporte la vérité terrain, le fine-tuning apporte le format et la rigueur.

**Objectif mesurable pour la phase RAG :** que Q3 retourne « Terraform 1.1 »
avec la source citée, et une syntaxe `from`/`to` sans guillemets.
