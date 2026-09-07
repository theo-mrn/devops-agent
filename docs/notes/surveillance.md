# Surveillance par événements (modèle ArgoCD)

## Le problème du polling

Redemander l'état complet du cluster toutes les 60 secondes coûte cher pour
rien : l'immense majorité des sondages ne révèle aucun changement.

| approche | tokens/jour envoyés au modèle |
|---|---:|
| polling toutes les 60 s | ~397 000 |
| watch + filtrage | ~4 000 |

**Facteur 100.** Et surtout : on ne paie que quand quelque chose se passe.

## Le mécanisme

`kubectl get pods --watch` maintient une connexion ouverte et pousse les
changements. Aucun sondage, aucun coût côté API — le watch lui-même est gratuit.

**Le gain ne vient pas du watch, il vient du FILTRAGE.** Un cluster pousse des
centaines d'événements par minute : heartbeats, mises à jour de statut,
changements d'annotations. Presque tous sont sans intérêt.

```
watch → état en mémoire → transitions → filtres → événement
                                                      ↓
                                          agent (seulement ici)
```

Aucun appel au modèle dans cette chaîne, sauf la dernière flèche.

## Les trois filtres

**Seuil de redémarrages** (3 par défaut). Un redémarrage isolé est souvent
transitoire — rolling update, sonde trop stricte au démarrage. Réagir
immédiatement coûterait pour du bruit.

**Tolérance sur Pending** (5 min). Un pod en Pending pendant quelques secondes
fait du scheduling normal. Seul un Pending qui dure signale un vrai problème.

**Déduplication** (30 min). Un pod qui redémarre 12 fois ne doit pas déclencher
12 diagnostics payants. Le retour à la normale libère la déduplication : un pod
réparé puis recassé redéclenche bien.

Mesuré par les tests : **5 000 événements de pods sains → 0 alerte**.

## Bug trouvé par les tests

`overview` produit l'état `"OOMKilled (précédent)"` — un pod qui tourne mais a
été tué au cycle d'avant. La table `ETATS_ANORMAUX` ne contenait que
`"OOMKilled"`, et l'égalité stricte laissait passer le cas.

**Un pod tué par manque de mémoire passait donc inaperçu** — précisément ce que
l'outil doit attraper. Corrigé par une correspondance par préfixe.

## Usage

```bash
devops-agent watch                 # observation : affiche ce qui remonterait
devops-agent watch --diagnose      # déclenche l'agent (coûte de l'API)
devops-agent watch --duree 300     # arrêt automatique après 5 min
```

Le mode observation sert à **régler les seuils avant de brancher l'agent** :
on voit ce qui remonterait sans rien payer.

## Réglages

```bash
RAG_SEUIL_REDEMARRAGES=5   # moins sensible
RAG_DEDUP_SECONDES=3600    # une heure entre deux diagnostics du même problème
RAG_PENDING_TOLERE=600     # 10 min avant de signaler un Pending
```

## Limite connue

`kubectl --watch` ne surveille qu'un namespace à la fois. Avec
`RAG_NAMESPACES` configuré sur plusieurs valeurs, seul le premier est surveillé
et un avertissement s'affiche. Un vrai multi-namespace demanderait plusieurs
processus, ou le client Python de Kubernetes.
