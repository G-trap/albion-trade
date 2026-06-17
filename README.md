# Albion Trade — Scanner d'arbitrage & craft (serveur EUROPE)

Outil de trading pour **Albion Online** : repère les opportunités d'achat-revente entre
villes (city flipping) et la rentabilité du craft, à partir des prix de marché de
l'[Albion Online Data Project](https://www.albion-online-data.com/) (crowdsourcing).

## Dashboard en ligne

**[→ Ouvrir le dashboard ⚔️](https://silver-albion.streamlit.app/)**

[![Streamlit](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://silver-albion.streamlit.app/)

Deux onglets :
- **🚚 Transport** : arbitrage entre villes + itinéraires sûrs (sans zone rouge/noire),
  avec monture, poids réels, investissement et temps de trajet estimé.
- **🔨 Craft** : acheter ressources → crafter → revendre, avec focus, recette et icône
  de chaque item (données issues de [ao-bin-dumps](https://github.com/ao-data/ao-bin-dumps)).

## Lancer en local

```bash
pip install -r requirements.txt
streamlit run dashboard.py
```

## Outils en ligne de commande

```bash
python albion_arbitrage.py --max-age 12 --min-margin 15   # transport
python albion_craft.py --focus --min-margin 10            # craft
```

## Notes

- Serveur **EUROPE**. Les prix peuvent être périmés : le filtre de fraîcheur (`--max-age`)
  et le garde-fou anti-outlier (médiane de l'historique) limitent les faux profits.
- Aucune clé d'API requise (l'API AODP est publique).
- Les taxes (vente, mise en vente) sont configurables : elles changent selon les patchs.
