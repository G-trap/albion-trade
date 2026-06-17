# Albion Trade — Scanner d'arbitrage marché

Outil de trading pour Albion Online (serveur **EUROPE**). Objectif : repérer les
opportunités d'achat-revente entre villes (city flipping) à partir des prix de marché.

## Contexte métier

- L'économie d'Albion est 100% pilotée par les joueurs. On exploite les écarts de prix
  entre les 6 villes royales + Caerleon + Black Market.
- Albion **ne publie aucun prix** publiquement. La seule source est l'**Albion Online
  Data Project (AODP)**, alimenté en crowdsourcing par un client qui sniffe le marché
  consulté en jeu. Conséquence : **les prix peuvent être périmés**.
- Priorité du projet : le **transport entre villes** (meilleur ratio temps/argent).
  Pistes secondaires à développer : module craft (acheter ressources → crafter → revendre)
  et flip Black Market.

## Source de données — API AODP

- Base EUROPE : `https://europe.albion-online-data.com`
- Prix courants : `/api/v2/stats/prices/{item_ids}.json?locations=...&qualities=...`
- Historique (sell orders) : `/api/v2/stats/history/{item_ids}.json?time-scale=1|6|24`
- Or : `/api/v2/stats/gold.json?count=N`
- **Rate limits** : 180 req/min, 300 req/5min. Limite d'URL : 4096 caractères.
- Demander la **compression Gzip** pour tout service en continu.
- IDs d'items : dépôt `github.com/ao-data/ao-bin-dumps` → `formatted/items.txt`.
- Champs clés de la réponse : `sell_price_min` (+ `_date`), `buy_price_max` (+ `_date`),
  par `item_id` / `city` / `quality`.

## Règles de calcul (NON négociables)

- **Fraîcheur** : tout prix plus vieux que `--max-age` (défaut 24h) est écarté. Une grosse
  marge sur une donnée morte est un piège. Toujours afficher l'âge de chaque prix utilisé.
- **Anti-outlier** : un prix récent peut être absurde (ordre fantaisiste). On le compare à
  une référence = médiane des `avg_price` de l'historique, et on écarte tout prix hors de
  `[ref / facteur, ref × facteur]` (`--outlier-factor`, défaut 3 ; `--no-outlier-check` pour
  désactiver). Briques partagées : `fetch_history`, `build_reference_prices`, `is_price_sane`.
- **Achat, 2 stratégies** (symétriques de la revente, `--no-buy-order` pour ne garder que l'instant) :
  - `instant` : prendre l'ordre de vente le moins cher (`sell_price_min`), sans frais.
  - `ordre`   : poster un ordre d'achat (`buy_price_max`) → coût = prix × (1 + frais_mise).
    Moins cher mais lent/incertain (suppose l'ordre exécuté).
- **Revente, 2 stratégies** :
  - `ordre`   : poster un ordre de vente → revenu = prix × (1 − taxe_vente − frais_mise).
  - `instant` : dump dans les ordres d'achat (`buy_price_max`) → revenu = prix × (1 − taxe_vente).
- La taxe de vente ne s'applique qu'à la revente ; côté achat, seul l'ordre d'achat paie le frais de mise.
- **Taxes configurables** (elles changent selon les patchs SBI — ne jamais coder en dur) :
  taxe de vente 4% (Premium) ou 8% (sans), frais de mise en vente 2,5%. Défauts dans le code,
  surchargeables en CLI.

## Fichiers

- `albion_arbitrage.py` — scanner principal de transport entre villes (CLI, stdlib only).
- `albion_craft.py` — module craft : acheter ressources → crafter → revendre, avec
  return rate des stations. Réutilise `fetch_prices` + helpers de fraîcheur du scanner.
- `albion_items.py` — métadonnées des items depuis ao-bin-dumps (poids, focus de craft,
  recettes) avec cache disque `item_meta.json`. Icônes : `render.albiononline.com/v1/item/{id}.png`.
- `dashboard.py` — dashboard web Streamlit, 2 onglets : **Transport** (arbitrage + itinéraires
  sûrs, monture/poids/investissement) et **Craft** (focus, recette, où vendre, icônes).
- `requirements.txt` — dépendances.

## Commandes

```bash
python albion_arbitrage.py                    # scan défaut (ressources raffinées T4-T8)
python albion_arbitrage.py --max-age 12       # fraîcheur < 12h
python albion_arbitrage.py --min-margin 15    # marge nette mini 15%
python albion_arbitrage.py --no-premium       # taxe vente 8%
python albion_arbitrage.py --items T6_CLOTH T7_LEATHER
python albion_arbitrage.py --csv reco.csv     # export CSV (pratique en cron)
```

## Conventions de code

- Python 3.10+. Garder la stdlib autant que possible (urllib + gzip déjà utilisés) pour
  un déploiement VPS sans friction.
- Pas de secrets en dur. L'API AODP ne nécessite pas de clé.
- Respecter les rate limits : batcher les items sous 4096 car/URL, pauser entre requêtes.
- Toute nouvelle source de prix doit passer par le même filtre de fraîcheur.

## Roadmap (à attaquer avec Claude Code)

1. Dashboard web (Streamlit ou Flask + Caddy) : tableau interactif, refresh auto, tri/filtre.
2. Module craft : rentabilité acheter-ressources → crafter → revendre, avec return rate
   des stations et coût de focus.
3. Module Black Market : opportunités royales → BM Caerleon.
4. Cron + alertes (Telegram ?) sur opportunités au-dessus d'un seuil de marge.
