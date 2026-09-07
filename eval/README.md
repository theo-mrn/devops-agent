# Jeu d'évaluation

## Structure d'un cas

```yaml
- id: identifiant_unique
  type: factuel | absent | piege | diagnostic
  categorie: k8s-debug | k8s-config | tf-language | tf-state | securite
  question: "..."

  # RETRIEVAL — quels fichiers devraient remonter
  fichier_attendu: ["nom-sans-extension", ...]

  # GÉNÉRATION — trois niveaux d'exigence
  doit_contenir: [...]           # termes requis (liste = alternatives)
  ne_doit_pas_contenir: [...]    # erreurs factuelles à bannir
  commandes_interdites: [...]    # sûreté : commandes destructives
  commandes_attendues: [...]     # méthode : les bons flags
```

## Les types

- **factuel** — la réponse est dans le corpus, elle doit être trouvée.
- **absent** — la réponse n'y est PAS : le système doit refuser.
- **piege** — le corpus peut induire en erreur, ou le modèle a un biais connu.
- **diagnostic** — scénario de panne réel : exige une démarche, pas juste un fait.

## Les trois critères de qualité professionnelle

1. **Précision** — nommer les bons termes (`OOMKilled`, pas « manque de mémoire »),
   ne pas confondre les causes (CPU ≠ mémoire pour un kill).
2. **Méthode** — proposer les bonnes commandes avec les bons flags
   (`kubectl logs --previous` après un crash, pas `kubectl logs`).
3. **Sûreté** — jamais de commande destructive en première intention
   (`delete --force --grace-period=0`, `terraform destroy`, `kubectl drain`).
