# Maîtrise des coûts API

## D'où vient la facture d'un agent

Le coût ne vient **pas** du nombre d'appels, mais de la **répétition du
contexte**. Chaque tour renvoie tout l'historique : un `kubectl describe` de
1800 tokens obtenu au tour 1 est refacturé aux tours 2, 3, 4, 5 et 6.

Un diagnostic de 6 tours consommait **25 000 tokens plein tarif** pour
seulement 6 000 tokens d'information réelle.

## Le levier principal : cacher l'historique

Le cache Anthropic fonctionne **par préfixe** : marquer le dernier message rend
cachable tout ce qui précède — système, définitions d'outils, historique complet.

```python
# loop.py — _avec_cache()
blocs[-1] = {**blocs[-1], "cache_control": {"type": "ephemeral"}}
```

Le piège évité : ne pas modifier `self.messages` en place. Une copie est
annotée pour l'appel, l'historique reste propre pour le tour suivant.

## Résultat mesuré

Scénario : 6 tours, 5 outils appelés (describe, logs, doc, fichier, get).

| modèle | sans cache | avec cache | gain |
|---|---:|---:|---:|
| haiku-4.5 | 0,0340 $ | **0,0149 $** | 56% |
| sonnet-5 | 0,0680 $ | **0,0299 $** | 56% |
| opus-5 | 0,1701 $ | **0,0747 $** | 56% |

**À 50 diagnostics/jour avec Sonnet : 45 $/mois au lieu de 91 $.**

Répartition après optimisation : 22 900 tokens lus en cache (à 10% du prix),
6 120 écrits (à 125%, rentabilisés dès la deuxième lecture).

⚠️ Le cache expire après 5 minutes d'inactivité. Sur des diagnostics espacés,
le gain est moindre — mais l'écriture reste rentable dès qu'un tour suit.

## Ce qui ne coûte rien : l'aperçu de cluster

`overview.py` ne contient **aucun appel au modèle**. C'est du `kubectl` et du
filtrage Python. La règle appliquée partout :

> tout ce qui est déterministe reste en Python,
> l'IA n'intervient que pour l'interprétation.

Compter des pods, trier par redémarrages, formater — du code classique fait ça
mieux : reproductible, instantané, gratuit, testable.

Et l'aperçu condensé économise directement : **276 tokens pour 453 pods** contre
~80 000 pour un dump brut, soit 0,16 $ économisés à chaque question sur Sonnet.

## Garde-fou budgétaire

`RAG_BUDGET_MAX` (0,50 $ par défaut) arrête l'agent si un diagnostic s'emballe.
Il rend alors ce qu'il a recueilli plutôt que de continuer à facturer.

```bash
RAG_BUDGET_MAX=0.10 make diagnose Q="..."
```

## Autres leviers, par ordre d'efficacité

1. **Choisir le bon modèle.** Haiku coûte 5× moins qu'Opus. À tester avec le jeu
   d'évaluation avant de trancher — Haiku est le plus faible sur l'orchestration
   multi-outils, ce qu'un agent demande précisément.
2. **Limiter les tours** (`TOURS_MAX`, 12 par défaut). Un diagnostic qui dépasse
   8 tours tourne généralement en rond.
3. **Tronquer les sorties d'outils.** Déjà en place : 8 000 caractères maximum
   par appel.
4. **Le mode RAG simple** (`ask`) coûte ~0,005 $ contre ~0,03 $ pour l'agent.
   Pour une question documentaire, il suffit.

## Suivi en temps réel

Chaque diagnostic affiche sa facture :

```
→ 6 tour(s) · 5 appel(s) d'outil · 34.2s
  0 frais + 22900 cache + 1000 générés · 96% depuis le cache · 0.0299 $
```
