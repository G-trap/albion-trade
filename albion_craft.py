#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Albion Online - Module craft : rentabilite acheter ressources -> crafter -> revendre
=====================================================================================

Calcule la rentabilite du craft a partir des prix de marche AODP (serveur EUROPE) :

    cout matieres = prix_ressource * quantite * (1 - return_rate)
    revenu        = prix_revente_item_crafte * (1 - taxe_vente [- frais_mise])
    profit        = revenu - cout_matieres - frais_craft - cout_focus

Reutilise telles quelles les briques de `albion_arbitrage.py` (recuperation des prix,
filtre de fraicheur, taxes) : memes regles non negociables, pas de duplication.

Le RETURN RATE (taux de retour de ressources des stations) reduit la quantite de
matieres reellement consommee. Valeurs par defaut :
    - 15.2 % : station de ville, sans focus, sans bonus de specialite
    - 43.5 % : avec focus (option --focus)
Ces taux changent selon les patchs SBI et le bonus de specialite de la ville
(ex : Martlock plate, Lymhurst leather...). Surcharge avec --return-rate.

Usage rapide :
    python albion_craft.py                         # scan recettes par defaut (armures+sacs T4-T8)
    python albion_craft.py --max-age 12            # ignore les prix > 12h
    python albion_craft.py --min-margin 10         # marge nette mini 10 %
    python albion_craft.py --focus                 # return rate focus (43.5 %)
    python albion_craft.py --return-rate 0.24      # bonus de ville (24.8 %)
    python albion_craft.py --no-premium            # taxe de vente 8 %
    python albion_craft.py --recipes recettes.json # recettes personnalisees (armes, artefacts)
    python albion_craft.py --csv craft.csv         # export CSV

Format du fichier --recipes (JSON) :
    {
      "T6_MAIN_SWORD":   {"components": {"T6_METALBAR": 20}},
      "T6_2H_CLAYMORE":  {"components": {"T6_METALBAR": 32}},
      "T5_OFF_SHIELD":   {"components": {"T5_METALBAR": 8, "T5_PLANKS": 8}}
    }
"""

import argparse
import csv
import json
import sys
from datetime import datetime, timezone

# Reutilisation directe du scanner d'arbitrage (memes regles fraicheur/taxes/reseau)
import albion_arbitrage as aa


# --------------------------------------------------------------------------- #
# CONFIG PAR DEFAUT
# --------------------------------------------------------------------------- #

# Return rate (taux de retour de ressources) des stations de craft.
DEFAULT_RETURN_RATE = 0.152        # station de ville, sans focus, sans bonus de specialite
DEFAULT_FOCUS_RETURN_RATE = 0.435  # avec focus (--focus)

# Quantites de ressources raffinees par craft (mecanique Albion standard).
QTY_HEAD = 8
QTY_ARMOR = 16
QTY_SHOES = 8
QTY_BAG = 8


def _build_default_recipes():
    """
    Recettes mono-ressource fiables et verifiables : armures (head/armor/shoes) en
    tissu / cuir / plaque + sacs, du T4 au T8. Un item T{n} consomme la ressource
    raffinee T{n} correspondante. Les armes et artefacts (multi-ressources) se
    chargent via --recipes.
    """
    recipes = {}
    families = [
        # (suffixe_type, ressource, quantite)
        ("HEAD_CLOTH_SET1", "CLOTH", QTY_HEAD),
        ("ARMOR_CLOTH_SET1", "CLOTH", QTY_ARMOR),
        ("SHOES_CLOTH_SET1", "CLOTH", QTY_SHOES),
        ("HEAD_LEATHER_SET1", "LEATHER", QTY_HEAD),
        ("ARMOR_LEATHER_SET1", "LEATHER", QTY_ARMOR),
        ("SHOES_LEATHER_SET1", "LEATHER", QTY_SHOES),
        ("HEAD_PLATE_SET1", "METALBAR", QTY_HEAD),
        ("ARMOR_PLATE_SET1", "METALBAR", QTY_ARMOR),
        ("SHOES_PLATE_SET1", "METALBAR", QTY_SHOES),
        ("BAG", "CLOTH", QTY_BAG),
    ]
    for tier in range(4, 9):  # T4 -> T8
        for suffix, res, qty in families:
            item_id = f"T{tier}_{suffix}"
            res_id = f"T{tier}_{res}"
            recipes[item_id] = {"components": {res_id: qty}}
    return recipes


DEFAULT_RECIPES = _build_default_recipes()


# --------------------------------------------------------------------------- #
# INDEX DES PRIX (avec filtre de fraicheur)
# --------------------------------------------------------------------------- #

def build_price_index(rows, max_age_hours, reference=None,
                      outlier_factor=aa.DEFAULT_OUTLIER_FACTOR):
    """
    Construit un index { (item_id, city, quality) -> infos } a partir des lignes AODP,
    en ne gardant que les prix respectant la fraicheur. Reutilise les helpers de parsing
    et d'age de `albion_arbitrage`.

    Si `reference` est fourni, tout prix hors de [ref/facteur, ref*facteur] est ecarte
    (garde-fou anti-outlier), aussi bien cote achat ressource que cote revente.
    """
    now = datetime.now(timezone.utc)
    index = {}
    for r in rows:
        item_id = r["item_id"]
        key = (item_id, r["city"], r.get("quality", 1))
        sell = r.get("sell_price_min", 0) or 0
        sell_age = aa._age_hours(aa._parse_date(r.get("sell_price_min_date")), now)
        buy = r.get("buy_price_max", 0) or 0
        buy_age = aa._age_hours(aa._parse_date(r.get("buy_price_max_date")), now)

        info = {"sell_min": None, "sell_age": None, "buy_max": None, "buy_age": None}
        if (sell > 0 and sell_age is not None and sell_age <= max_age_hours
                and aa.is_price_sane(sell, item_id, reference, outlier_factor)):
            info["sell_min"], info["sell_age"] = sell, sell_age
        if (buy > 0 and buy_age is not None and buy_age <= max_age_hours
                and aa.is_price_sane(buy, item_id, reference, outlier_factor)):
            info["buy_max"], info["buy_age"] = buy, buy_age
        index[key] = info
    return index


def _cheapest_buy(index, item_id, setup_fee, allow_buy_order=True, quality=1):
    """
    Cout d'achat le plus bas d'une ressource, 2 strategies (symetriques de la revente) :
      - 'instant' : on prend l'ordre de vente le moins cher (sell_price_min), sans frais
      - 'ordre'   : on poste un ordre d'achat (buy_price_max) -> cout = prix*(1+frais_mise)
    Renvoie (city, cost, age, strategy) ou None.
    """
    best = None  # (city, cost, age, strategy)
    for (iid, city, q), info in index.items():
        if iid != item_id or q != quality:
            continue
        if info["sell_min"] is not None:
            cand = (city, info["sell_min"], info["sell_age"], "instant")
            if best is None or cand[1] < best[1]:
                best = cand
        if allow_buy_order and info["buy_max"] is not None:
            cost = info["buy_max"] * (1 + setup_fee)
            cand = (city, cost, info["buy_age"], "ordre")
            if best is None or cand[1] < best[1]:
                best = cand
    return best


def _best_resale(index, item_id, quality, sales_tax, setup_fee):
    """
    Meilleur revenu net de revente de l'item crafte, toutes villes / 2 strategies :
      - 'ordre'   : poster un ordre de vente (sell_price_min) -> prix*(1-taxe-frais_mise)
      - 'instant' : dump dans les ordres d'achat (buy_price_max) -> prix*(1-taxe)
    Renvoie le meilleur candidat (revenu net le plus eleve) ou None.
    """
    best = None
    for (iid, city, q), info in index.items():
        if iid != item_id or q != quality:
            continue
        if info["sell_min"] is not None:
            revenue = info["sell_min"] * (1 - sales_tax - setup_fee)
            cand = {"city": city, "strategy": "ordre", "price": info["sell_min"],
                    "age": info["sell_age"], "revenue": revenue}
            if best is None or cand["revenue"] > best["revenue"]:
                best = cand
        if info["buy_max"] is not None:
            revenue = info["buy_max"] * (1 - sales_tax)
            cand = {"city": city, "strategy": "instant", "price": info["buy_max"],
                    "age": info["buy_age"], "revenue": revenue}
            if best is None or cand["revenue"] > best["revenue"]:
                best = cand
    return best


# --------------------------------------------------------------------------- #
# CALCUL DE RENTABILITE CRAFT
# --------------------------------------------------------------------------- #

def find_craft_opportunities(rows, recipes, sales_tax, setup_fee, max_age_hours,
                             return_rate, craft_fee=0.0, focus_cost=0.0, out_quality=1,
                             reference=None, outlier_factor=aa.DEFAULT_OUTLIER_FACTOR,
                             allow_buy_order=True):
    """
    Pour chaque recette, calcule la rentabilite acheter-ressources -> crafter -> revendre.

    cout_matieres = somme_composants( cout_achat_ressource * quantite * (1 - return_rate) )
    profit        = revenu_revente - cout_matieres - frais_craft - cout_focus
    marge %       = profit / cout_total * 100   (cout_total = matieres + frais + focus)

    Achat des ressources, 2 strategies (voir _cheapest_buy) : 'instant' (sell_price_min)
    ou 'ordre' (buy_price_max + frais de mise), la moins chere etant retenue.
    Les ressources sont prises en qualite 1 ; l'item crafte est revendu en `out_quality`.
    Si `reference` est fourni, le garde-fou anti-outlier ecarte les prix aberrants.
    """
    index = build_price_index(rows, max_age_hours, reference, outlier_factor)
    opportunities = []

    for out_item, recipe in recipes.items():
        components = recipe.get("components", {})
        if not components:
            continue

        # --- cout des matieres ---
        material_cost = 0.0
        buy_cities = []
        buy_strategies = []
        mat_age = 0.0
        missing = False
        for res_id, qty in components.items():
            cheap = _cheapest_buy(index, res_id, setup_fee, allow_buy_order, quality=1)
            if cheap is None:
                missing = True
                break
            city, price, age, strategy = cheap
            material_cost += price * qty * (1 - return_rate)
            buy_cities.append(city)
            buy_strategies.append(strategy)
            if age is not None:
                mat_age = max(mat_age, age)
        if missing:
            continue

        total_cost = material_cost + craft_fee + focus_cost
        if total_cost <= 0:
            continue

        # --- meilleure revente de l'item crafte ---
        resale = _best_resale(index, out_item, out_quality, sales_tax, setup_fee)
        if resale is None:
            continue

        profit = resale["revenue"] - total_cost
        if profit <= 0:
            continue

        margin = profit / total_cost * 100.0
        # ville d'achat : unique si mono-ressource, sinon liste compacte
        uniq = sorted(set(buy_cities))
        buy_city_label = uniq[0] if len(uniq) == 1 else "+".join(uniq)
        # strategie d'achat : unique si homogene, sinon 'mixte'
        uniq_strat = set(buy_strategies)
        buy_strategy_label = uniq_strat.pop() if len(uniq_strat) == 1 else "mixte"

        opportunities.append({
            "item": out_item,
            "buy_city": buy_city_label,
            "buy_strategy": buy_strategy_label,
            "material_cost": round(material_cost),
            "total_cost": round(total_cost),
            "buy_age_h": round(mat_age, 1),
            "sell_city": resale["city"],
            "sell_price": round(resale["price"]),
            "sell_age_h": round(resale["age"], 1) if resale["age"] is not None else None,
            "strategy": resale["strategy"],
            "profit": round(profit),
            "margin_pct": round(margin, 1),
        })

    return opportunities


# --------------------------------------------------------------------------- #
# AFFICHAGE
# --------------------------------------------------------------------------- #

def print_table(opps, top, return_rate):
    if not opps:
        print("\nAucun craft rentable trouve (essaie --max-age plus eleve, --focus, "
              "ou une autre liste de recettes).")
        return
    opps = sorted(opps, key=lambda o: o["profit"], reverse=True)[:top]

    header = (f"{'ITEM CRAFTE':<22} {'RES@':<14}{'COUT TOT':>11} {'ACH':>7}  "
              f"{'VENTE@':<14}{'PRIX V':>11} {'VTE':>7}  "
              f"{'PROFIT':>10}  {'MARGE':>6}  {'AGE':>6}")
    print(f"\n(return rate = {return_rate*100:.1f} %)")
    print(header)
    print("-" * len(header))
    for o in opps:
        ages = [a for a in (o["buy_age_h"], o["sell_age_h"]) if a is not None]
        age = max(ages) if ages else 0.0
        print(f"{o['item']:<22} {o['buy_city']:<14}{o['total_cost']:>11,} {o['buy_strategy']:>7}  "
              f"{o['sell_city']:<14}{o['sell_price']:>11,} {o['strategy']:>7}  "
              f"{o['profit']:>10,}  {o['margin_pct']:>5}%  {age:>5.1f}h")
    print("-" * len(header))
    print("COUT TOT = matieres (apres return rate) + frais craft + focus. ACH = achat ressources "
          "('instant' = ordre de vente le moins cher ; 'ordre' = ordre d'achat + frais).")
    print("VTE = revente ('instant' = dump dans les ordres d'achat ; 'ordre' = ordre de vente).")
    print("AGE = anciennete max de la donnee utilisee. Plus c'est vieux, moins c'est fiable.")


def export_csv(opps, path):
    if not opps:
        return
    keys = ["item", "buy_city", "buy_strategy", "material_cost", "total_cost", "buy_age_h",
            "sell_city", "sell_price", "sell_age_h", "strategy", "profit", "margin_pct"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for o in sorted(opps, key=lambda x: x["profit"], reverse=True):
            w.writerow(o)
    print(f"\nCSV ecrit : {path}")


# --------------------------------------------------------------------------- #
# MAIN
# --------------------------------------------------------------------------- #

def load_recipes(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # validation minimale
    clean = {}
    for item_id, recipe in data.items():
        comps = recipe.get("components") if isinstance(recipe, dict) else None
        if not comps:
            print(f"  [recettes] '{item_id}' ignore (pas de 'components')", file=sys.stderr)
            continue
        clean[item_id] = {"components": {k: int(v) for k, v in comps.items()}}
    return clean


def main():
    ap = argparse.ArgumentParser(
        description="Module craft Albion Online (EUROPE) : acheter ressources -> crafter -> revendre")
    ap.add_argument("--recipes", default=None,
                    help="Fichier JSON de recettes (defaut : armures + sacs T4-T8)")
    ap.add_argument("--cities", nargs="+", default=aa.CITIES, help="Villes a scanner")
    ap.add_argument("--quality", type=int, default=1,
                    help="Qualite de revente de l'item crafte (1=normal)")
    ap.add_argument("--sales-tax", type=float, default=None,
                    help="Taxe de vente (defaut 0.04 premium)")
    ap.add_argument("--no-premium", action="store_true",
                    help="Taxe de vente a 8%% (sans Premium)")
    ap.add_argument("--setup-fee", type=float, default=aa.DEFAULT_SETUP_FEE,
                    help="Frais de mise en vente, strategie 'ordre' (defaut 0.025)")
    ap.add_argument("--return-rate", type=float, default=None,
                    help=f"Return rate des stations (defaut {DEFAULT_RETURN_RATE}, "
                         f"{DEFAULT_FOCUS_RETURN_RATE} avec --focus)")
    ap.add_argument("--focus", action="store_true",
                    help="Utilise le return rate focus (43.5%%) au lieu de 15.2%%")
    ap.add_argument("--craft-fee", type=float, default=0.0,
                    help="Frais de craft de la station, en silver par craft (defaut 0)")
    ap.add_argument("--focus-cost", type=float, default=0.0,
                    help="Cout du focus en silver equivalent par craft (defaut 0)")
    ap.add_argument("--max-age", type=float, default=aa.DEFAULT_MAX_AGE_HOURS,
                    help="Age max des prix en heures (defaut 24)")
    ap.add_argument("--min-margin", type=float, default=0.0,
                    help="Marge nette minimale en %% pour afficher")
    ap.add_argument("--top", type=int, default=25, help="Nombre de lignes a afficher")
    ap.add_argument("--csv", default=None, help="Chemin d'export CSV")
    ap.add_argument("--outlier-factor", type=float, default=aa.DEFAULT_OUTLIER_FACTOR,
                    help="Tolerance anti-outlier : prix valide dans [ref/f, ref*f] "
                         f"(defaut {aa.DEFAULT_OUTLIER_FACTOR})")
    ap.add_argument("--no-outlier-check", action="store_true",
                    help="Desactive le garde-fou historique (plus rapide, moins fiable)")
    ap.add_argument("--no-buy-order", action="store_true",
                    help="Achat des ressources en instantane uniquement (pas d'ordre d'achat)")
    args = ap.parse_args()

    sales_tax = args.sales_tax if args.sales_tax is not None \
        else (0.08 if args.no_premium else aa.DEFAULT_SALES_TAX)
    if args.return_rate is not None:
        return_rate = args.return_rate
    else:
        return_rate = DEFAULT_FOCUS_RETURN_RATE if args.focus else DEFAULT_RETURN_RATE

    recipes = load_recipes(args.recipes) if args.recipes else DEFAULT_RECIPES
    if not recipes:
        print("Aucune recette a scanner.", file=sys.stderr)
        sys.exit(1)

    # On recupere en une passe les prix des items crafts ET de leurs ressources.
    item_ids = set(recipes.keys())
    for recipe in recipes.values():
        item_ids.update(recipe["components"].keys())
    item_ids = sorted(item_ids)

    print(f"Serveur     : EUROPE ({aa.BASE_URL})", file=sys.stderr)
    print(f"Recettes    : {len(recipes)} | Items a prix : {len(item_ids)} | "
          f"Villes : {len(args.cities)}", file=sys.stderr)
    print(f"Return rate : {return_rate*100:.1f}%{' (focus)' if args.focus else ''}", file=sys.stderr)
    print(f"Taxes       : vente {sales_tax*100:.1f}% + mise en vente {args.setup_fee*100:.1f}%",
          file=sys.stderr)
    print(f"Fraicheur   : <= {args.max_age}h\n", file=sys.stderr)
    print("Recuperation des prix...", file=sys.stderr)

    rows = aa.fetch_prices(item_ids, args.cities)
    if not rows:
        print("\nAucune donnee recue. Verifie ta connexion / les IDs d'items.", file=sys.stderr)
        sys.exit(1)

    reference = None
    if not args.no_outlier_check:
        print("Recuperation de l'historique (garde-fou anti-outlier)...", file=sys.stderr)
        history = aa.fetch_history(item_ids, args.cities)
        reference = aa.build_reference_prices(history)
        print(f"Reference de prix construite pour {len(reference)} items.\n", file=sys.stderr)

    opps = find_craft_opportunities(
        rows, recipes, sales_tax, args.setup_fee, args.max_age,
        return_rate, craft_fee=args.craft_fee, focus_cost=args.focus_cost,
        out_quality=args.quality, reference=reference, outlier_factor=args.outlier_factor,
        allow_buy_order=not args.no_buy_order)
    opps = [o for o in opps if o["margin_pct"] >= args.min_margin]

    print_table(opps, args.top, return_rate)
    if args.csv:
        export_csv(opps, args.csv)


if __name__ == "__main__":
    main()
