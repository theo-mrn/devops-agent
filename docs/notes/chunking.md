# Brique 2 — Chunking

Document témoin : `data/raw/terraform-moved.mdx` (2 462 car., 56 lignes).
Choisi parce qu'il contient la réponse que le modèle avait inventée en baseline.

## Résultats

|                | naïf (500 car.) | structurel |
|----------------|----------------:|-----------:|
| chunks         | 6               | 5          |
| **abîmés**     | **3**           | **0**      |
| taille min     | 212             | 176        |
| taille max     | 500             | 1166       |

## Ce que casse le découpage naïf

- **Chunk 2** : tableau décapité, se termine sur `| Argument | Description | Ty`
  — coupé au milieu du mot « Type ». En-tête et données séparés.
- **Chunk 4** : commence par `red |`, fin orpheline du mot « required ».
- **Chunk 0** : à moitié rempli de frontmatter (`created_at`, `last_modified`).
  Du bruit pur qui part à l'embedding et dégrade le vecteur.
- **Le pire** : le tableau des types et le contexte qui l'explique se retrouvent
  dans deux chunks différents. Une recherche qui remonte l'un sans l'autre
  donne au modèle une information mutilée → il hallucine à nouveau.

## Les trois règles du découpage structurel

1. **Retirer le frontmatter** — métadonnées, pur bruit.
2. **Couper aux titres `##`** — jamais à l'intérieur d'une section.
3. **Blocs ``` atomiques** — masqués avant découpage, restaurés après.

Conséquence : les tailles deviennent **variables** (176 → 1166 car.).
C'est voulu. Une unité de sens n'a pas de taille fixe ; la régularité du
découpage naïf était le symptôme de son indifférence au contenu.

## Limite constatée

Le détecteur heuristique a marqué « ok » un chunk naïf pourtant tronqué en fin
(chunk 3). **Les compteurs automatiques ratent des cas** — inspecter les chunks
à l'œil reste nécessaire.

## À retenir

La qualité du RAG est plafonnée par la qualité du chunking. Aucun reranker,
aucun modèle plus gros ne rattrape une information coupée en deux à
l'indexation. C'est la première chose à soigner, et la moins spectaculaire.
