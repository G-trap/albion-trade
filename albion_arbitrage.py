#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Albion Online - Scanner d'arbitrage marche (serveur EUROPE)
============================================================

Recupere les prix via l'API publique de l'Albion Online Data Project (AODP)
et calcule les meilleures opportunites de transport (acheter ville A -> revendre ville B).

Source des donnees : https://www.albion-online-data.com/  (crowdsourcing, non officiel)

Usage rapide :
    python albion_arbitrage.py                       # scan avec la liste d'items par defaut
    python albion_arbitrage.py --max-age 12          # ignore les prix > 12h
    python albion_arbitrage.py --min-margin 15       # marge nette mini 15%
    python albion_arbitrage.py --no-premium          # taxe de vente a 8% au lieu de 4%
    python albion_arbitrage.py --items T6_CLOTH T7_CLOTH T8_CLOTH
    python albion_arbitrage.py --csv resultats.csv   # export CSV
    python albion_arbitrage.py --top 30

ATTENTION sur les taxes : les frais de marche d'Albion changent au fil des patchs.
Verifie en jeu et ajuste --sales-tax / --setup-fee si besoin.
"""

import argparse
import csv
import gzip
import io
import sys
import time
from datetime import datetime, timezone
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# --------------------------------------------------------------------------- #
# CONFIG PAR DEFAUT
# --------------------------------------------------------------------------- #

BASE_URL = "https://europe.albion-online-data.com"  # serveur EUROPE
PRICES_ENDPOINT = "/api/v2/stats/prices/{items}.json"
HISTORY_ENDPOINT = "/api/v2/stats/history/{items}.json"

# Villes scannees (royales + Caerleon + Black Market).
# "Brecilien" est dispo si tu joues l'Avalon ; retire-la si pas utile.
CITIES = [
    "Caerleon",
    "Bridgewatch",
    "Lymhurst",
    "Martlock",
    "Thetford",
    "Fort Sterling",
    "Brecilien",
    "Black Market",
]

# Liste d'items par defaut : ressources raffinees T4->T8 (gros volume, bonne couverture AODP).
# Tu peux passer ta propre liste avec --items. IDs complets : github.com/ao-data/ao-bin-dumps
DEFAULT_ITEMS = []
for tier in range(4, 9):  # T4 a T8
    for base in ("CLOTH", "LEATHER", "METALBAR", "PLANKS", "STONEBLOCK"):
        DEFAULT_ITEMS.append(f"T{tier}_{base}")

# Frais de marche (modifiables en ligne de commande)
DEFAULT_SALES_TAX = 0.04    # 4% avec Premium (8% sans -> --no-premium)
DEFAULT_SETUP_FEE = 0.025   # 2.5% de frais de mise en vente (ordre)

# Fraicheur des donnees
DEFAULT_MAX_AGE_HOURS = 24  # on ignore tout prix plus vieux que ca

# Garde-fou anti-outlier : un prix courant doit rester dans
# [reference / facteur, reference * facteur], ou reference = mediane des avg_price
# de l'historique. Au-dela, on considere l'ordre comme aberrant (faux profit).
DEFAULT_OUTLIER_FACTOR = 3.0

# Limites API AODP
MAX_URL_LEN = 3900          # marge sous la limite reelle de 4096
RATE_SLEEP = 0.4            # pause entre requetes (180 req/min = 1 toutes les 0.33s)


# --------------------------------------------------------------------------- #
# RECUPERATION DES DONNEES
# --------------------------------------------------------------------------- #

def _http_get_json(url):
    """GET avec gzip, renvoie le JSON parse."""
    req = Request(url, headers={
        "User-Agent": "albion-arbitrage-scanner/1.0",
        "Accept-Encoding": "gzip",
    })
    with urlopen(req, timeout=30) as resp:
        raw = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
    import json
    return json.loads(raw.decode("utf-8"))


def _batch_items(items, locations_param):
    """Decoupe la liste d'items pour rester sous la limite de longueur d'URL."""
    batches, current = [], []
    # longueur fixe : base + endpoint + ?locations=...
    fixed = len(BASE_URL) + len(PRICES_ENDPOINT) + len("?locations=") + len(locations_param)
    length = fixed
    for it in items:
        add = len(it) + 1  # +1 pour la virgule
        if current and length + add > MAX_URL_LEN:
            batches.append(current)
            current, length = [], fixed
        current.append(it)
        length += add
    if current:
        batches.append(current)
    return batches


def fetch_prices(items, cities):
    """Recupere tous les prix pour items x villes. Renvoie une liste de dicts AODP."""
    locations_param = quote(",".join(cities), safe=",")
    all_rows = []
    batches = _batch_items(items, locations_param)
    for i, batch in enumerate(batches, 1):
        items_param = quote(",".join(batch), safe=",")
        url = BASE_URL + PRICES_ENDPOINT.format(items=items_param) + f"?locations={locations_param}"
        try:
            data = _http_get_json(url)
            all_rows.extend(data)
            print(f"  [batch {i}/{len(batches)}] {len(batch)} items -> {len(data)} lignes", file=sys.stderr)
        except HTTPError as e:
            print(f"  [batch {i}] erreur HTTP {e.code} : {e.reason}", file=sys.stderr)
        except URLError as e:
            print(f"  [batch {i}] erreur reseau : {e.reason}", file=sys.stderr)
        if i < len(batches):
            time.sleep(RATE_SLEEP)
    return all_rows


def fetch_history(items, cities, time_scale=24):
    """
    Recupere l'historique des prix (sell orders agreges) pour items x villes.
    Sert a construire un prix de reference robuste pour le garde-fou anti-outlier.
    Renvoie une liste de series AODP : {item_id, location, quality, data: [...]}.
    """
    locations_param = quote(",".join(cities), safe=",")
    all_rows = []
    batches = _batch_items(items, locations_param)
    for i, batch in enumerate(batches, 1):
        items_param = quote(",".join(batch), safe=",")
        url = (BASE_URL + HISTORY_ENDPOINT.format(items=items_param)
               + f"?locations={locations_param}&time-scale={time_scale}")
        try:
            data = _http_get_json(url)
            all_rows.extend(data)
            print(f"  [hist {i}/{len(batches)}] {len(batch)} items -> {len(data)} series",
                  file=sys.stderr)
        except HTTPError as e:
            print(f"  [hist {i}] erreur HTTP {e.code} : {e.reason}", file=sys.stderr)
        except URLError as e:
            print(f"  [hist {i}] erreur reseau : {e.reason}", file=sys.stderr)
        if i < len(batches):
            time.sleep(RATE_SLEEP)
    return all_rows


# --------------------------------------------------------------------------- #
# GARDE-FOU ANTI-OUTLIER (prix de reference via historique)
# --------------------------------------------------------------------------- #

def _median(values):
    vals = sorted(v for v in values if v is not None and v > 0)
    n = len(vals)
    if n == 0:
        return None
    mid = n // 2
    if n % 2:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / 2.0


def build_reference_prices(history_rows):
    """
    A partir des series d'historique AODP, calcule un prix de reference par item :
    la mediane de tous les avg_price observes (robuste aux ordres aberrants).
    Renvoie { item_id -> reference }.
    """
    by_item = {}
    for r in history_rows:
        item_id = r.get("item_id")
        if not item_id:
            continue
        for point in (r.get("data") or []):
            ap = point.get("avg_price")
            if ap:
                by_item.setdefault(item_id, []).append(ap)
    reference = {}
    for item_id, vals in by_item.items():
        m = _median(vals)
        if m:
            reference[item_id] = m
    return reference


def is_price_sane(price, item_id, reference, factor):
    """
    True si `price` est plausible au regard du prix de reference de l'item.
    Sans reference (None ou item absent), on ne peut pas juger -> on laisse passer.
    """
    if not reference:
        return True
    ref = reference.get(item_id)
    if not ref:
        return True
    return (ref / factor) <= price <= (ref * factor)


# --------------------------------------------------------------------------- #
# CALCUL D'ARBITRAGE
# --------------------------------------------------------------------------- #

def _parse_date(s):
    """Parse une date AODP en datetime aware (UTC)."""
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _age_hours(dt, now):
    if dt is None:
        return None
    return (now - dt).total_seconds() / 3600.0


def find_opportunities(rows, sales_tax, setup_fee, max_age_hours, quality=1,
                       reference=None, outlier_factor=DEFAULT_OUTLIER_FACTOR,
                       allow_buy_order=True):
    """
    Pour chaque item, trouve la meilleure ville d'achat et de revente, en tenant compte
    des taxes et de la fraicheur. Choisit la meilleure combinaison achat x revente.

    Si `reference` est fourni (mediane de l'historique par item), tout prix hors de
    [reference / outlier_factor, reference * outlier_factor] est ecarte comme aberrant.

    Deux strategies d'ACHAT (symetriques de la revente) :
      - 'instant': on prend l'ordre de vente le moins cher (sell_price_min)
                   -> cout = prix   [pas de frais de mise]
      - 'ordre'  : on poste un ordre d'achat au niveau buy_price_max et on attend
                   -> cout = prix * (1 + frais_mise)   [active par allow_buy_order]
    Deux strategies de REVENTE :
      - 'ordre'  : on poste un ordre de vente au prix sell_price_min de la ville cible
                   -> revenu = prix * (1 - taxe_vente - frais_mise_en_vente)
      - 'instant': on dump dans les ordres d'achat existants (buy_price_max)
                   -> revenu = prix * (1 - taxe_vente)   [pas de frais de mise]
    La taxe de vente ne s'applique qu'a la revente ; cote achat, seul l'ordre d'achat
    paie le frais de mise. L'achat 'ordre' est optimiste (suppose ton ordre execute).
    """
    now = datetime.now(timezone.utc)

    # regroupe par item
    by_item = {}
    for r in rows:
        if r.get("quality", 1) != quality:
            continue
        by_item.setdefault(r["item_id"], []).append(r)

    opportunities = []
    for item_id, entries in by_item.items():
        # candidats ACHAT : 2 strategies (instant via sell_price_min, ordre via buy_price_max)
        buy_candidates = []  # (city, cost, age, strategy)
        for e in entries:
            city = e["city"]
            # achat instant : on prend l'ordre de vente le moins cher, sans frais
            sp = e.get("sell_price_min", 0)
            sp_age = _age_hours(_parse_date(e.get("sell_price_min_date")), now)
            if (sp and sp > 0 and sp_age is not None and sp_age <= max_age_hours
                    and is_price_sane(sp, item_id, reference, outlier_factor)):
                buy_candidates.append((city, sp, sp_age, "instant"))
            # achat par ordre : on poste un ordre d'achat (buy_price_max) + frais de mise
            if allow_buy_order:
                bp = e.get("buy_price_max", 0)
                bp_age = _age_hours(_parse_date(e.get("buy_price_max_date")), now)
                if (bp and bp > 0 and bp_age is not None and bp_age <= max_age_hours
                        and is_price_sane(bp, item_id, reference, outlier_factor)):
                    buy_candidates.append((city, bp * (1 + setup_fee), bp_age, "ordre"))
        if not buy_candidates:
            continue
        buy_city, buy_price, buy_age, buy_strategy = min(buy_candidates, key=lambda x: x[1])

        # candidats REVENTE
        best = None
        for e in entries:
            city = e["city"]
            if city == buy_city:
                continue  # pas d'arbitrage dans la meme ville ici

            # strategie ordre de vente
            sp = e.get("sell_price_min", 0)
            sp_age = _age_hours(_parse_date(e.get("sell_price_min_date")), now)
            if (sp and sp > 0 and sp_age is not None and sp_age <= max_age_hours
                    and is_price_sane(sp, item_id, reference, outlier_factor)):
                revenue = sp * (1 - sales_tax - setup_fee)
                profit = revenue - buy_price
                cand = {
                    "sell_city": city, "strategy": "ordre", "sell_price": sp,
                    "sell_age": sp_age, "profit": profit,
                }
                if best is None or cand["profit"] > best["profit"]:
                    best = cand

            # strategie instant (dump dans buy orders)
            bp = e.get("buy_price_max", 0)
            bp_age = _age_hours(_parse_date(e.get("buy_price_max_date")), now)
            if (bp and bp > 0 and bp_age is not None and bp_age <= max_age_hours
                    and is_price_sane(bp, item_id, reference, outlier_factor)):
                revenue = bp * (1 - sales_tax)
                profit = revenue - buy_price
                cand = {
                    "sell_city": city, "strategy": "instant", "sell_price": bp,
                    "sell_age": bp_age, "profit": profit,
                }
                if best is None or cand["profit"] > best["profit"]:
                    best = cand

        if best is None or best["profit"] <= 0:
            continue

        margin = best["profit"] / buy_price * 100.0
        opportunities.append({
            "item": item_id,
            "buy_city": buy_city,
            "buy_price": round(buy_price),
            "buy_age_h": round(buy_age, 1),
            "buy_strategy": buy_strategy,
            "sell_city": best["sell_city"],
            "sell_price": round(best["sell_price"]),
            "sell_age_h": round(best["sell_age"], 1),
            "strategy": best["strategy"],
            "profit": round(best["profit"]),
            "margin_pct": round(margin, 1),
        })

    return opportunities


# --------------------------------------------------------------------------- #
# AFFICHAGE
# --------------------------------------------------------------------------- #

def print_table(opps, top):
    if not opps:
        print("\nAucune opportunite rentable trouvee (essaie --max-age plus eleve "
              "ou une autre liste d'items).")
        return
    opps = sorted(opps, key=lambda o: o["profit"], reverse=True)[:top]

    header = (f"{'ITEM':<15} {'ACHAT@':<14}{'PRIX A':>10} {'ACH':>7}  "
              f"{'VENTE@':<14}{'PRIX V':>10} {'VTE':>7}  "
              f"{'PROFIT/u':>10}  {'MARGE':>6}  {'AGE':>6}")
    print("\n" + header)
    print("-" * len(header))
    for o in opps:
        age = max(o["buy_age_h"], o["sell_age_h"])
        print(f"{o['item']:<15} {o['buy_city']:<14}{o['buy_price']:>10,} {o['buy_strategy']:>7}  "
              f"{o['sell_city']:<14}{o['sell_price']:>10,} {o['strategy']:>7}  "
              f"{o['profit']:>10,}  {o['margin_pct']:>5}%  {age:>5.1f}h")
    print("-" * len(header))
    print("ACH = strategie d'achat ('instant' = ordre de vente le moins cher ; "
          "'ordre' = tu postes un ordre d'achat, moins cher mais plus lent/incertain).")
    print("VTE = strategie de revente ('instant' = dump dans les ordres d'achat ; "
          "'ordre' = tu postes un ordre de vente, plus rentable mais plus lent).")
    print("AGE = anciennete max de la donnee utilisee. Plus c'est vieux, moins c'est fiable.")


def export_csv(opps, path):
    if not opps:
        return
    keys = ["item", "buy_city", "buy_price", "buy_age_h", "buy_strategy", "sell_city",
            "sell_price", "sell_age_h", "strategy", "profit", "margin_pct"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for o in sorted(opps, key=lambda x: x["profit"], reverse=True):
            w.writerow(o)
    print(f"\nCSV ecrit : {path}")


# --------------------------------------------------------------------------- #
# MAIN
# --------------------------------------------------------------------------- #

def main():
    ap = argparse.ArgumentParser(description="Scanner d'arbitrage Albion Online (EUROPE)")
    ap.add_argument("--items", nargs="+", default=DEFAULT_ITEMS,
                    help="Liste d'IDs d'items (defaut : ressources raffinees T4-T8)")
    ap.add_argument("--cities", nargs="+", default=CITIES, help="Villes a scanner")
    ap.add_argument("--quality", type=int, default=1, help="Qualite (1=normal, 2=bon, ... 5=chef d'oeuvre)")
    ap.add_argument("--sales-tax", type=float, default=None, help="Taxe de vente (defaut 0.04 premium)")
    ap.add_argument("--no-premium", action="store_true", help="Taxe de vente a 8%% (sans Premium)")
    ap.add_argument("--setup-fee", type=float, default=DEFAULT_SETUP_FEE, help="Frais de mise en vente (defaut 0.025)")
    ap.add_argument("--max-age", type=float, default=DEFAULT_MAX_AGE_HOURS, help="Age max des prix en heures (defaut 24)")
    ap.add_argument("--min-margin", type=float, default=0.0, help="Marge nette minimale en %% pour afficher")
    ap.add_argument("--top", type=int, default=25, help="Nombre de lignes a afficher")
    ap.add_argument("--csv", default=None, help="Chemin d'export CSV")
    ap.add_argument("--outlier-factor", type=float, default=DEFAULT_OUTLIER_FACTOR,
                    help="Tolerance anti-outlier : prix valide dans [ref/f, ref*f] "
                         f"(defaut {DEFAULT_OUTLIER_FACTOR})")
    ap.add_argument("--no-outlier-check", action="store_true",
                    help="Desactive le garde-fou historique (plus rapide, moins fiable)")
    ap.add_argument("--no-buy-order", action="store_true",
                    help="Achat instantane uniquement (n'envisage pas l'ordre d'achat)")
    args = ap.parse_args()

    sales_tax = args.sales_tax if args.sales_tax is not None else (0.08 if args.no_premium else DEFAULT_SALES_TAX)

    print(f"Serveur   : EUROPE ({BASE_URL})", file=sys.stderr)
    print(f"Items     : {len(args.items)} | Villes : {len(args.cities)}", file=sys.stderr)
    print(f"Taxes     : vente {sales_tax*100:.1f}% + mise en vente {args.setup_fee*100:.1f}%", file=sys.stderr)
    print(f"Fraicheur : <= {args.max_age}h\n", file=sys.stderr)
    print("Recuperation des prix...", file=sys.stderr)

    rows = fetch_prices(args.items, args.cities)
    if not rows:
        print("\nAucune donnee recue. Verifie ta connexion / les IDs d'items.", file=sys.stderr)
        sys.exit(1)

    reference = None
    if not args.no_outlier_check:
        print("Recuperation de l'historique (garde-fou anti-outlier)...", file=sys.stderr)
        history = fetch_history(args.items, args.cities)
        reference = build_reference_prices(history)
        print(f"Reference de prix construite pour {len(reference)} items.\n", file=sys.stderr)

    opps = find_opportunities(rows, sales_tax, args.setup_fee, args.max_age, args.quality,
                              reference=reference, outlier_factor=args.outlier_factor,
                              allow_buy_order=not args.no_buy_order)
    opps = [o for o in opps if o["margin_pct"] >= args.min_margin]

    print_table(opps, args.top)
    if args.csv:
        export_csv(opps, args.csv)


if __name__ == "__main__":
    main()
