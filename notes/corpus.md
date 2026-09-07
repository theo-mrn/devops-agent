# Brique 6 — Élargissement du corpus

De **1 document (5 chunks)** à **40 documents (387 chunks)**, 490 Ko.

## Sources retenues

| préfixe | contenu | chunks |
|---|---|---:|
| `k8s-pods` | cycle de vie, probes, QoS, init containers | 144 |
| `tf-language` | langage Terraform, `moved`, style | 79 |
| `k8s-config` | ressources, ConfigMap, Secret | 68 |
| `k8s-debug` | diagnostic de pods et services | 53 |
| `tf-state` | state, locking, refactoring | 43 |

Choix délibéré de **ne pas cloner toute la doc** : un corpus trop gros noie le
signal et rend l'inspection manuelle impossible.

## Ce que l'élargissement a résolu — et pas résolu

✅ **exit code 137 / OOMKilled** : maintenant couvert
(`determine-reason-pod-failure`, `manage-resources-containers`).

❌ **version du bloc `moved`** : toujours absente. La doc de référence ne
mentionne jamais les versions d'introduction — cette info vit dans le CHANGELOG.

→ **Élargir un corpus ne bouche pas les trous automatiquement.** Il faut savoir
*quelle source* contient quelle information.

## Score de référence — enfin discriminant

```
Hit Rate @3    90%      (était 100% sur 5 chunks)
MRR            0.850    (était 1.000)
Génération     12/12
  dont refus   2/2
```

Le 100% de la brique 5 était un artefact : avec 5 chunks et top-3, on retenait
60% du corpus. Ici on en retient **0,8%**. Le score mesure enfin quelque chose.

## Échec analysé : `comportement_state`

Question : « Terraform détruit-il la ressource avec `moved` ? »
Remonté : `purpose`, `refactor`, `refactor` — **pas** `moved.mdx`.

Vérification manuelle : `moved.mdx` contient la réponse exacte (« Terraform does
not destroy the resource »), `refactor.mdx` parle de `removed`/`import`, un
mécanisme différent. **L'échec est réel.**

→ Cas typique que BM25 corrigerait : le mot exact « moved » aurait pesé lourd.

## ⚠️ Faux positif détecté dans l'évaluation

Le même cas a un retrieval **RATÉ** mais une génération **OK**.

Réponse produite : « Non, Terraform ne détruit pas la ressource quand on utilise
`terraform state mv` » — or la question portait sur le **bloc `moved`**, pas sur
la commande CLI `state mv`. Deux mécanismes différents.

Mon test cherchait le mot « non », l'a trouvé, a validé. **Le test est passé sur
une réponse hors sujet.**

→ Les métriques par mots-clés vérifient la **présence de termes**, pas la
**pertinence**. Bon marché et reproductibles, mais elles ne remplacent pas la
lecture des réponses.

## Limite confirmée du dense seul

| question | résultat |
|---|---|
| « Qu'est-ce qu'un OOMKilled ? » | trouve `manage-resources-containers` ✅ |
| « exit code 137 » | remonte 3× `pod-lifecycle`, rate les bonnes sources ❌ |

Le modèle ne fait pas le lien 137 → SIGKILL → OOM. Même défaut qu'en brique 3.
**BM25 devrait corriger ces deux cas** (`moved`, `137`) : ce sont des termes exacts.

## Correction d'une surinterprétation

J'avais avancé que l'« écart entre le 1er et le dernier chunk » signalait la
confiance du retrieval. **Faux** : la question Terraform qui *réussit* a un écart
plus faible (0,032) que la question 137 qui *échoue* (0,069). Indicateur à ignorer.

## Performance

- encodage de 387 chunks : **30,5 s** sur MPS (12,7 chunks/s)
- index sur disque : **2,0 Mo**
- recherche : ~7 ms une fois l'index chargé
- évaluation complète (12 cas, avec LLM) : 96 s
