"""Découpage structurel des documents."""

from devops_agent.ingestion import chunking


DOCUMENT = """---
title: Test
---

# Titre principal

Introduction du document.

## Première section

Du texte avec un bloc de code :

```yaml
apiVersion: v1
kind: Pod

metadata:
  name: test
```

## Seconde section

| Colonne | Valeur |
| --- | --- |
| a | 1 |
"""


def test_frontmatter_retire():
    chunks = chunking.decouper(DOCUMENT)
    assert all("title: Test" not in c["texte"] for c in chunks)


def test_decoupe_aux_sections():
    titres = [c["titre"] for c in chunking.decouper(DOCUMENT)]
    assert "Première section" in titres
    assert "Seconde section" in titres


def test_bloc_code_jamais_coupe():
    for c in chunking.decouper(DOCUMENT):
        # Un nombre impair de ``` signale un bloc tronqué.
        assert c["texte"].count("```") % 2 == 0


def test_tableau_reste_entier():
    chunks = chunking.decouper(DOCUMENT)
    avec_tableau = [c for c in chunks if "| a | 1 |" in c["texte"]]
    assert avec_tableau, "le tableau a disparu"
    assert "| --- |" in avec_tableau[0]["texte"], "en-tête du tableau perdu"
