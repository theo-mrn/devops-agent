# Réduction du bruit

## Le constat

Après quelques heures sur le cluster réel : **9 diagnostics, 0,69 $**.

```
4x  [grave   ] SansEndpoint        même service, cluster mono-instance
3x  [critique] OOMKilled           même pod, un seul incident
1x  [critique] AucunReplicaPret    l'agent diagnostiquant son propre redémarrage
1x  [grave   ] Failed              job Trivy éphémère
```

**Sept sur neuf étaient du bruit.**

## Trois causes, trois corrections

### La déduplication ne survivait pas aux redémarrages

Elle vivait en mémoire. Chaque redémarrage du pod — mise à jour d'image,
changement de configuration — la perdait et rediagnostiquait tout. D'où les
4× `SansEndpoint` et 3× `OOMKilled` sur les mêmes objets.

Elle est désormais **persistée** à côté du journal, sur le volume. Un test
vérifie qu'une anomalie déjà signalée ne l'est pas à nouveau après
redémarrage.

### La fenêtre était trop courte

30 minutes convenaient à un incident passager, pas à une anomalie
structurelle : un service mono-instance sans endpoint le restera des semaines.

Portée à **6 heures**.

### Un job qui échoue n'est pas un incident

Les scans Trivy, les tâches ponctuelles réessayées : `Failed` est fréquent et
souvent normal. Reclassé de `grave` à `surveillance`.

## Le webhook ne transmet plus que l'essentiel

```
RAG_WEBHOOK_GRAVITE=grave    # nouveau défaut
```

`surveillance` regroupe le bruit de fond — jobs éphémères, replicas
temporairement incomplets. Ces anomalies restent visibles dans les logs et le
journal, mais n'encombrent plus Discord.

Trois niveaux disponibles :

| seuil | transmet |
|---|---|
| `surveillance` | tout |
| `grave` (défaut) | anomalies réelles |
| `critique` | uniquement ce qui exige une action immédiate |

## Effet attendu

Sur les 9 diagnostics observés, il en resterait **2** : le premier
`SansEndpoint` et le premier `OOMKilled`. Les répétitions sont supprimées par
la déduplication persistée, le job Trivy par le seuil de gravité, et
l'auto-diagnostic par l'exclusion de namespace ajoutée en v0.2.1.

Coût ramené de 0,69 $ à environ 0,09 $ pour la même période.
