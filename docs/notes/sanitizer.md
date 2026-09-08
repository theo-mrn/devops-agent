# Masquage des secrets

## La faille

Sans filtre, `kubectl logs` et `kubectl get -o yaml` expédient vers une API
externe tout ce qu'ils contiennent : jetons, mots de passe, clés privées,
chaînes de connexion. Sur un cluster de production, c'est une fuite réelle.

Repris de k-AI, qui avait traité le sujet — notre projet ne l'avait pas.

## Deux différences avec l'implémentation de k-AI

**L'étiquette est conservée.**

```
DATABASE_PASSWORD=hunter2  →  DATABASE_PASSWORD=[SECRET MASQUÉ]
```

Le modèle sait qu'un mot de passe existe à cet endroit — ce qui peut compter
pour le diagnostic — sans jamais en voir la valeur. k-AI masquait la ligne
entière, étiquette comprise.

**La troncature garde le début ET la fin.** La fin d'un log porte souvent
l'erreur fatale ; la couper reviendrait à perdre l'information la plus utile.

## Couverture

| catégorie | exemples |
|---|---|
| formats reconnaissables | JWT, clé privée PEM, `ghp_`/`glpat_`, `AKIA…`, `dckr_pat_` |
| webhooks | Slack, Discord — l'URL entière est un secret |
| URL de connexion | `postgres://user:pass@host` → identifiants masqués, hôte gardé |
| en-têtes HTTP | `Authorization: Bearer …` |
| kubeconfig | `client-key-data`, `certificate-authority-data` |
| clé/valeur générique | tout nom contenant `token`, `password`, `secret`, `api_key`, `auth`… |

## Point de passage unique

Le masquage est appliqué dans `tools.executer()` **et nulle part ailleurs**.
Le faire dans chaque fonction d'outil exposerait au premier oubli.

L'aperçu de cluster contourne `executer()` : il masque explicitement, et un
test vérifie que cette précaution reste en place.

## Deux pièges rencontrés

### Le marqueur se faisait re-masquer

`Authorization: Bearer eyJ…` produisait `[SECRET MASQUÉ] MASQUÉ]` : le motif
JWT s'appliquait au marqueur déjà inséré par le motif Authorization.

Correction : un jeton neutre (`\x00SECRET\x00`) est utilisé pendant le
traitement, remplacé par le marqueur lisible à la fin.

### Ordre troncature / masquage

Masquer d'abord semblait plus sûr. Mesuré : appliquer douze expressions
régulières à 50 000 caractères prend **82 secondes**.

L'ordre a été inversé — tronquer, puis masquer — et la garantie tient :
seul le texte conservé part vers l'API, et il est intégralement masqué.
Un secret situé dans la portion supprimée n'atteint jamais le modèle
puisqu'il a été **supprimé**, pas seulement caché.

Après correction : **3 ms** sur un log réel de 8 000 caractères.

## Faux positifs

Masquer une valeur utile nuit au diagnostic. Sont explicitement préservés :
`replicas=3`, `memory: 512Mi`, `image: nginx:1.21`, `exitCode: 137`,
`reason: OOMKilled`, `token: null`.

10 cas de test couvrent cette frontière.
