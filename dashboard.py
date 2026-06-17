#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Albion Online - Dashboard Streamlit (serveur EUROPE)
=====================================================

Interface web par-dessus les modules du projet. Deux onglets :
  - Transport : arbitrage entre villes (albion_arbitrage) + itineraires surs.
  - Craft     : acheter ressources -> crafter -> revendre (albion_craft), avec focus,
                recette et icone de chaque item (poids/focus/recette via ao-bin-dumps).

Memes regles que les modules CLI (fraicheur, taxes, garde-fou anti-outlier).

Lancement :
    streamlit run dashboard.py
Dependances :
    pip install streamlit pandas requests
"""

import pandas as pd
import streamlit as st

import albion_arbitrage as aa
import albion_craft as ac
import albion_items as ai

# Villes royales reliees entre elles sans traverser de zone rouge ou noire.
SAFE_CITIES = ["Bridgewatch", "Lymhurst", "Martlock", "Thetford", "Fort Sterling"]

# Pastille de couleur par ville (repere visuel facon carte d'Albion).
CITY_COLORS = {
    "Thetford": "🟣", "Fort Sterling": "⚪", "Lymhurst": "🟢", "Bridgewatch": "🟡",
    "Martlock": "🔵", "Caerleon": "🔴", "Black Market": "⚫", "Brecilien": "🟤",
}


def city_badge(city):
    """Prefixe une ville par sa pastille de couleur (None -> chaine vide)."""
    if not city:
        return city
    return f"{CITY_COLORS.get(city, '•')} {city}"


# Strategie d'achat/vente avec icone (eclair = instantane, parchemin = ordre poste).
STRAT_BADGES = {"instant": "⚡ instant", "ordre": "📜 ordre", "mixte": "🔀 mixte"}


def strat_badge(s):
    return STRAT_BADGES.get(s, s)

# Anneau des 5 villes royales autour de Caerleon -> estimation du temps de trajet.
TRAVEL_RING = ["Fort Sterling", "Martlock", "Lymhurst", "Bridgewatch", "Thetford"]
TRAVEL_MINUTES_BY_DISTANCE = {1: 11, 2: 19}  # estimation indicative


def travel_minutes(city_a, city_b):
    """Temps de trajet de reference (minutes) entre deux villes sures. None si inconnu."""
    if city_a == city_b:
        return 0
    try:
        ia, ib = TRAVEL_RING.index(city_a), TRAVEL_RING.index(city_b)
    except ValueError:
        return None
    raw = abs(ia - ib)
    dist = min(raw, len(TRAVEL_RING) - raw)
    return TRAVEL_MINUTES_BY_DISTANCE.get(dist)


# Presets de montures : facteur de vitesse (x temps de reference) + charge (kg).
MOUNTS = {
    "Cheval de selle (rapide, peu de charge)": {"speed": 0.55, "capacity": 500},
    "Cheval / Direwolf (equilibre)":           {"speed": 0.75, "capacity": 700},
    "Boeuf de transport T5 (reference)":        {"speed": 1.00, "capacity": 1800},
    "Boeuf de transport T8":                    {"speed": 1.10, "capacity": 2700},
    "Mammouth de transport T8 (lent, max)":     {"speed": 1.50, "capacity": 5000},
    "Personnalise":                             None,
}
DEFAULT_MOUNT = "Boeuf de transport T5 (reference)"
DEFAULT_UNIT_WEIGHT = 0.5  # poids de secours (kg) si un item est absent du dump


# --------------------------------------------------------------------------- #
# RECUPERATION (cache pour ne pas spammer l'API a chaque interaction)
# --------------------------------------------------------------------------- #

@st.cache_data(ttl=300, show_spinner="Recuperation des prix AODP...")
def load_prices(items, cities):
    return aa.fetch_prices(list(items), list(cities))


@st.cache_data(ttl=900, show_spinner="Recuperation de l'historique (anti-outlier)...")
def load_reference(items, cities):
    history = aa.fetch_history(list(items), list(cities))
    return aa.build_reference_prices(history)


@st.cache_data(ttl=86400, show_spinner="Chargement des metadonnees (ao-bin-dumps)...")
def load_meta():
    """Poids / focus / recettes des items (cache disque + cache Streamlit 24h)."""
    return ai.load_meta()


def icon_col_config(label="Icone"):
    return st.column_config.ImageColumn(label, width="small")


def ornament():
    """Separateur ornemental dore."""
    st.markdown('<div class="albion-orn">✦ &nbsp; ✦ &nbsp; ✦</div>', unsafe_allow_html=True)


# --------------------------------------------------------------------------- #
# THEME ALBION (or sur fond sombre)
# --------------------------------------------------------------------------- #

ALBION_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@600;700&family=EB+Garamond:ital@0;1&display=swap');

:root {
  --gold: #c9a227; --gold-light: #e3c869; --panel: #241c14; --parch: #ece3d0;
}

.stApp {
  background:
    radial-gradient(1100px 500px at 50% -8%, rgba(201,162,39,0.10), transparent 60%),
    linear-gradient(180deg, #15110c 0%, #100d09 100%);
}
[data-testid="stHeader"] { background: transparent; }

h1, h2, h3, h4 { font-family: 'Cinzel', serif !important; color: var(--gold-light) !important;
                 letter-spacing: .4px; }
.stApp, p, label, span, div, li { font-family: 'EB Garamond', Georgia, serif; }

/* Banniere d'en-tete */
.albion-header {
  position: relative; display:flex; align-items:center; gap:24px;
  border: 1px solid rgba(201,162,39,.45); border-radius: 12px;
  padding: 20px 30px; margin: 4px 0 16px 0;
  background:
    radial-gradient(700px 140px at 50% 0%, rgba(201,162,39,.12), transparent 70%),
    repeating-linear-gradient(135deg, rgba(201,162,39,.035) 0 2px, transparent 2px 9px),
    linear-gradient(135deg, rgba(40,31,21,.96), rgba(19,15,10,.96));
  box-shadow: 0 6px 24px rgba(0,0,0,.55), inset 0 0 0 1px rgba(201,162,39,.18),
              inset 0 0 44px rgba(201,162,39,.07);
}
.albion-header::before, .albion-header::after {
  content: "❖"; position: absolute; top: 10px; color: rgba(201,162,39,.55); font-size: .9rem;
}
.albion-header::before { left: 12px; }
.albion-header::after { right: 12px; }
.albion-emblem { flex:0 0 auto; line-height:0;
  filter: drop-shadow(0 3px 7px rgba(0,0,0,.55)); }
.albion-htext { flex:1 1 auto; }
.albion-header .title { font-family:'Cinzel',serif; font-size:2.3rem; font-weight:700;
  color: var(--gold-light); margin:0; letter-spacing:2px; text-shadow: 0 2px 8px rgba(0,0,0,.6); }
.albion-header .subtitle { color:#bcae90; font-size:1rem; margin-top:6px; }
.albion-header .rule { height:2px; margin-top:12px; border-radius:2px;
  background: linear-gradient(90deg, transparent, var(--gold), transparent); }

/* Separateur ornemental */
.albion-orn { text-align:center; color: rgba(201,162,39,.55); letter-spacing:6px;
  margin:16px 0 6px 0; font-size:.85rem; }

/* Metrics en cartes ornees */
[data-testid="stMetric"] {
  background: linear-gradient(180deg, rgba(36,28,20,.85), rgba(26,20,14,.85));
  border: 1px solid rgba(201,162,39,.35); border-radius: 10px; padding: 12px 16px;
  box-shadow: inset 0 0 18px rgba(201,162,39,.05);
}
[data-testid="stMetricValue"] { color: var(--gold-light) !important; font-family:'Cinzel',serif; }
[data-testid="stMetricLabel"] { color:#cbbd9b !important; }

/* Sidebar */
[data-testid="stSidebar"] {
  background: linear-gradient(180deg, #1b150e, #131009);
  border-right: 1px solid rgba(201,162,39,.30);
}

/* Onglets */
.stTabs [data-baseweb="tab-list"] { gap:6px; border-bottom:1px solid rgba(201,162,39,.25); }
.stTabs [data-baseweb="tab"] { background: rgba(36,28,20,.6);
  border:1px solid rgba(201,162,39,.25); border-bottom:none; border-radius:8px 8px 0 0;
  color:#cbbd9b; font-family:'Cinzel',serif; padding:8px 18px; }
.stTabs [aria-selected="true"] { background: rgba(201,162,39,.18);
  color: var(--gold-light) !important; border-color: rgba(201,162,39,.55); }

/* Boutons */
.stButton > button, .stDownloadButton > button {
  background: linear-gradient(180deg, #caa42b, #9c7d1e); color:#1a1409 !important;
  border:1px solid #e3c869; font-weight:700; border-radius:8px;
  font-family:'Cinzel',serif; letter-spacing:.3px;
}
.stButton > button:hover, .stDownloadButton > button:hover { filter:brightness(1.1); }

/* Cadres : dataframes, expanders, alertes */
[data-testid="stDataFrame"], [data-testid="stExpander"] {
  border:1px solid rgba(201,162,39,.28); border-radius:10px;
}
[data-testid="stExpander"] summary { color: var(--gold-light); font-family:'Cinzel',serif; }
hr { border-color: rgba(201,162,39,.25) !important; }

/* Alertes (success/info/warning) : liseret dore */
[data-testid="stAlert"], [data-testid="stAlertContainer"] {
  border:1px solid rgba(201,162,39,.40) !important; border-radius:8px;
}

/* Sliders et controles : accent dore */
[data-testid="stSlider"] [role="slider"] { box-shadow: 0 0 0 3px rgba(201,162,39,.25); }

/* Scrollbar doree */
::-webkit-scrollbar { width:10px; height:10px; }
::-webkit-scrollbar-track { background:#15110c; }
::-webkit-scrollbar-thumb { background:linear-gradient(180deg,#7a611a,#caa42b);
  border-radius:6px; border:2px solid #15110c; }

/* Pied de page ornemental */
.albion-footer { text-align:center; color:#8c7f63; font-family:'Cinzel',serif;
  margin-top:22px; padding-top:12px; font-size:.85rem;
  border-top:1px solid rgba(201,162,39,.25); }
.albion-footer .gem { color: var(--gold); }
</style>
"""


# Embleme heraldique (ecu + epees croisees + piece d'or), SVG inline = sans dependance.
EMBLEM_SVG = """
<svg width="84" height="92" viewBox="0 0 84 92" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <linearGradient id="ecu" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#2c2118"/><stop offset="1" stop-color="#140f08"/>
    </linearGradient>
    <linearGradient id="or" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#e9d089"/><stop offset="1" stop-color="#a9851d"/>
    </linearGradient>
  </defs>
  <path d="M42 5 L77 14 V42 C77 64 59 80 42 87 C25 80 7 64 7 42 V14 Z"
        fill="url(#ecu)" stroke="url(#or)" stroke-width="3.5"/>
  <path d="M42 11 L72 18 V42 C72 60 57 74 42 81 C27 74 12 60 12 42 V18 Z"
        fill="none" stroke="rgba(233,208,137,.35)" stroke-width="1.2"/>
  <g stroke="#dcd0b4" stroke-width="3.2" stroke-linecap="round">
    <line x1="26" y1="30" x2="58" y2="66"/><line x1="58" y1="30" x2="26" y2="66"/>
  </g>
  <g stroke="url(#or)" stroke-width="4.5" stroke-linecap="round">
    <line x1="20" y1="34" x2="32" y2="25"/><line x1="52" y1="25" x2="64" y2="34"/>
  </g>
  <circle cx="42" cy="50" r="9" fill="url(#or)" stroke="#7a611a" stroke-width="1.6"/>
  <circle cx="42" cy="50" r="5" fill="none" stroke="rgba(122,97,26,.7)" stroke-width="1.1"/>
</svg>
"""


def inject_theme():
    st.markdown(ALBION_CSS, unsafe_allow_html=True)
    st.markdown(
        '<div class="albion-header">'
        '<div class="albion-emblem">' + EMBLEM_SVG + '</div>'
        '<div class="albion-htext">'
        '<div class="title">⚔️ ALBION TRADE</div>'
        '<div class="subtitle">Transport &amp; Craft — serveur EUROPE · '
        'donnees Albion Online Data Project (les prix peuvent etre perimes : '
        'surveille l\'age et garde le garde-fou anti-outlier actif).</div>'
        '<div class="rule"></div>'
        '</div>'
        '</div>',
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------- #
# ONGLET TRANSPORT
# --------------------------------------------------------------------------- #

def render_transport(p, meta):
    cities, items = p["cities"], p["items"]
    try:
        rows = load_prices(tuple(items), tuple(cities))
    except Exception as e:  # noqa: BLE001
        st.error(f"Erreur lors de la recuperation des prix : {e}")
        return
    if not rows:
        st.error("Aucune donnee recue. Verifie ta connexion ou les IDs d'items.")
        return

    reference = None
    if p["outlier_check"]:
        try:
            reference = load_reference(tuple(items), tuple(cities))
        except Exception as e:  # noqa: BLE001
            st.warning(f"Historique indisponible, garde-fou desactive : {e}")

    opps = aa.find_opportunities(
        rows, p["sales_tax"], p["setup_fee"], p["max_age"], p["quality"],
        reference=reference, outlier_factor=p["outlier_factor"],
        allow_buy_order=p["allow_buy_order"],
    )
    opps = [o for o in opps if o["margin_pct"] >= p["min_margin"]]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("📦 Items scannes", len(items))
    c2.metric("📊 Lignes de prix", len(rows))
    c3.metric("⚖️ Opportunites", len(opps))
    c4.metric("🏆 Meilleure marge", f"{max((o['margin_pct'] for o in opps), default=0):.1f} %")

    guard = (f"anti-outlier x{p['outlier_factor']:.1f} ({len(reference)} refs)"
             if reference else "anti-outlier off")
    st.caption(
        f"Taxes : vente {p['sales_tax']*100:.1f}% + mise {p['setup_fee']*100:.1f}% | "
        f"Fraicheur ≤ {p['max_age']:.0f}h | Marge ≥ {p['min_margin']:.0f}% | "
        f"Qualite {p['quality']} | {guard}"
    )

    if not opps:
        st.info("Aucune opportunite rentable avec ces filtres. Augmente la fraicheur, "
                "baisse la marge minimale ou change la liste d'items.")
        return

    df = pd.DataFrame(opps)
    df["age_max_h"] = df[["buy_age_h", "sell_age_h"]].max(axis=1)
    df["icon"] = df["item"].map(ai.icon_url)
    df["buy_city"] = df["buy_city"].map(city_badge)
    df["sell_city"] = df["sell_city"].map(city_badge)
    df["buy_strategy"] = df["buy_strategy"].map(strat_badge)
    df["strategy"] = df["strategy"].map(strat_badge)
    df = df.rename(columns={
        "icon": "Icone", "item": "Item", "buy_city": "Achat @", "buy_price": "Prix achat",
        "buy_strategy": "Achat", "sell_city": "Vente @", "sell_price": "Prix vente",
        "strategy": "Vente", "profit": "Profit/u", "margin_pct": "Marge %",
        "age_max_h": "Age max (h)",
    })
    df = df[[
        "Icone", "Item", "Achat @", "Prix achat", "Achat", "Vente @", "Prix vente",
        "Vente", "Profit/u", "Marge %", "Age max (h)",
    ]].sort_values("Profit/u", ascending=False)

    marge_max = max(float(df["Marge %"].max()), 1.0)
    st.subheader("Opportunites (cliquer sur un en-tete pour trier)")
    st.dataframe(
        df, use_container_width=True, hide_index=True,
        column_config={
            "Icone": icon_col_config(),
            "Prix achat": st.column_config.NumberColumn(format="%d"),
            "Prix vente": st.column_config.NumberColumn(format="%d"),
            "Profit/u": st.column_config.NumberColumn(format="%d"),
            "Marge %": st.column_config.ProgressColumn(
                "Marge %", format="%.1f %%", min_value=0, max_value=marge_max),
            "Age max (h)": st.column_config.NumberColumn(format="%.1f"),
        },
    )
    st.download_button(
        "⬇️ Exporter en CSV", data=df.drop(columns="Icone").to_csv(index=False).encode("utf-8"),
        file_name="albion_transport.csv", mime="text/csv",
    )
    st.caption(
        "**Achat** : 'instant' = ordre de vente le moins cher ; 'ordre' = tu postes un ordre "
        "d'achat (moins cher, plus lent). **Vente** : 'instant' = dump dans les ordres d'achat ; "
        "'ordre' = tu postes un ordre de vente. AGE = anciennete max de la donnee."
    )

    render_safe_routes(opps, p, meta)


def render_safe_routes(opps, p, meta):
    ornament()
    st.subheader("🛡️ Itineraires surs (bleu + jaune, sans full-loot)")
    st.caption(
        "Trajets entre villes royales relies uniquement par des zones bleues et jaunes : "
        "aucune zone rouge ni noire, donc **tu ne perds jamais ta cargaison**. "
        f"Villes : {', '.join(SAFE_CITIES)}. Exclut Caerleon, Black Market et Brecilien."
    )

    safe = [o for o in opps if o["buy_city"] in SAFE_CITIES and o["sell_city"] in SAFE_CITIES]
    if not safe:
        st.info("Aucun trajet bleu/jaune rentable. Les meilleures marges passent souvent "
                "par Caerleon / Black Market (rouge). Baisse la marge ou augmente la fraicheur.")
        return

    cap, speed, fb = p["capacity_kg"], p["speed_factor"], p["fallback_weight"]
    for o in safe:
        w = ai.get_weight(o["item"], meta, default=fb)
        o["_weight"] = w
        o["_units"] = max(1, int(cap / w))
        o["_trip_profit"] = o["_units"] * o["profit"]
        o["_trip_invest"] = o["_units"] * o["buy_price"]

    routes = {}
    for o in safe:
        routes.setdefault((o["buy_city"], o["sell_city"]), []).append(o)

    recap = []
    for (buy, sell), lst in routes.items():
        lst.sort(key=lambda x: x["_trip_profit"], reverse=True)
        base = travel_minutes(buy, sell)
        minutes = round(base * speed) if base else None
        top = lst[0]
        per_min = round(top["_trip_profit"] / minutes) if minutes else None
        recap.append({
            "Icone": ai.icon_url(top["item"]),
            "Itineraire": f"{city_badge(buy)} → {city_badge(sell)}", "Temps (min)": minutes,
            "Items rentables": len(lst), "Meilleur item": top["item"],
            "Unites/voyage": top["_units"], "Investissement/voyage": top["_trip_invest"],
            "Profit/voyage": top["_trip_profit"], "Profit/voyage par min": per_min,
            "Meilleure marge %": max(x["margin_pct"] for x in lst), "_key": (buy, sell),
        })
    recap.sort(key=lambda r: (r["Profit/voyage par min"] or 0), reverse=True)

    best = recap[0]
    temps_txt = f"~{best['Temps (min)']} min de trajet" if best["Temps (min)"] else "duree inconnue"
    st.success(
        f"Meilleur itineraire sur : **{best['Itineraire']}** avec **{p['mount_name']}** "
        f"({p['capacity_kg']:,} kg) — **{best['Profit/voyage']:,} silver/voyage** en chargeant "
        f"{best['Unites/voyage']:,}x {best['Meilleur item']} (investissement "
        f"**{best['Investissement/voyage']:,} silver**, {best['Meilleure marge %']:.0f}% marge), "
        f"{temps_txt}."
    )

    recap_df = pd.DataFrame(recap).drop(columns="_key")
    recap_marge_max = max(float(recap_df["Meilleure marge %"].max()), 1.0)
    st.dataframe(
        recap_df, use_container_width=True, hide_index=True,
        column_config={
            "Icone": icon_col_config(),
            "Temps (min)": st.column_config.NumberColumn(format="%d min"),
            "Unites/voyage": st.column_config.NumberColumn(format="%d"),
            "Investissement/voyage": st.column_config.NumberColumn(format="%d"),
            "Profit/voyage": st.column_config.NumberColumn(format="%d"),
            "Profit/voyage par min": st.column_config.NumberColumn(format="%d /min"),
            "Meilleure marge %": st.column_config.ProgressColumn(
                "Meilleure marge %", format="%.1f %%", min_value=0, max_value=recap_marge_max),
        },
    )

    for r in recap:
        buy, sell = r["_key"]
        t_lbl = f" · ~{r['Temps (min)']} min" if r["Temps (min)"] else ""
        with st.expander(f"{r['Itineraire']}{t_lbl} — {r['Items rentables']} item(s) a transporter"):
            d = pd.DataFrame(routes[(buy, sell)])
            d["icon"] = d["item"].map(ai.icon_url)
            d["buy_strategy"] = d["buy_strategy"].map(strat_badge)
            d["strategy"] = d["strategy"].map(strat_badge)
            d = d[["icon", "item", "buy_price", "buy_strategy", "sell_price", "strategy",
                   "profit", "margin_pct", "_weight", "_units", "_trip_invest", "_trip_profit"]
                  ].rename(columns={
                      "icon": "Icone", "item": "Item", "buy_price": "Prix achat",
                      "buy_strategy": "Achat", "sell_price": "Prix vente", "strategy": "Vente",
                      "profit": "Profit/u", "margin_pct": "Marge %", "_weight": "Poids (kg)",
                      "_units": "Unites/voyage", "_trip_invest": "Investissement/voyage",
                      "_trip_profit": "Profit/voyage",
                  }).sort_values("Profit/voyage", ascending=False)
            st.dataframe(
                d, use_container_width=True, hide_index=True,
                column_config={
                    "Icone": icon_col_config(),
                    "Prix achat": st.column_config.NumberColumn(format="%d"),
                    "Prix vente": st.column_config.NumberColumn(format="%d"),
                    "Profit/u": st.column_config.NumberColumn(format="%d"),
                    "Marge %": st.column_config.NumberColumn(format="%.1f %%"),
                    "Poids (kg)": st.column_config.NumberColumn(format="%.2f"),
                    "Unites/voyage": st.column_config.NumberColumn(format="%d"),
                    "Investissement/voyage": st.column_config.NumberColumn(format="%d"),
                    "Profit/voyage": st.column_config.NumberColumn(format="%d"),
                },
            )

    st.caption(
        "Poids reels (ao-bin-dumps) : Profit/voyage = monture remplie de l'item qui maximise "
        "le gain. Investissement/voyage = capital a sortir. Temps ajuste par la vitesse de la "
        "monture. Itineraires tries par profit/voyage par minute (ratio temps/argent)."
    )


# --------------------------------------------------------------------------- #
# ONGLET CRAFT
# --------------------------------------------------------------------------- #

def render_craft(p, meta):
    cities = p["cities"]

    # Recettes reelles depuis le dump (ressources + quantites) pour les items choisis.
    recipes, unknown = {}, []
    for it in p["craft_items"]:
        comps = ai.get_recipe(it, meta)
        if comps:
            recipes[it] = {"components": comps}
        else:
            unknown.append(it)
    if unknown:
        st.warning("Items sans recette dans le dump (ignores) : " + ", ".join(unknown))
    if not recipes:
        st.info("Aucune recette a scanner. Saisis des IDs d'items craftables dans la barre laterale.")
        return

    price_items = sorted(set(recipes) | {r for rec in recipes.values() for r in rec["components"]})
    try:
        rows = load_prices(tuple(price_items), tuple(cities))
    except Exception as e:  # noqa: BLE001
        st.error(f"Erreur lors de la recuperation des prix : {e}")
        return
    if not rows:
        st.error("Aucune donnee recue. Verifie ta connexion ou les IDs d'items.")
        return

    reference = None
    if p["outlier_check"]:
        try:
            reference = load_reference(tuple(price_items), tuple(cities))
        except Exception as e:  # noqa: BLE001
            st.warning(f"Historique indisponible, garde-fou desactive : {e}")

    opps = ac.find_craft_opportunities(
        rows, recipes, p["sales_tax"], p["setup_fee"], p["max_age"], p["return_rate"],
        craft_fee=p["craft_fee"], focus_cost=p["focus_cost"], out_quality=p["quality"],
        reference=reference, outlier_factor=p["outlier_factor"],
        allow_buy_order=p["allow_buy_order"],
    )
    opps = [o for o in opps if o["margin_pct"] >= p["min_margin"]]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("📜 Recettes", len(recipes))
    c2.metric("🔨 Crafts rentables", len(opps))
    c3.metric("♻️ Return rate", f"{p['return_rate']*100:.1f} %")
    c4.metric("🏆 Meilleure marge", f"{max((o['margin_pct'] for o in opps), default=0):.1f} %")

    focus_txt = "avec focus" if p["use_focus"] else "sans focus"
    st.caption(
        f"Acheter ressources → crafter → revendre. Return rate {p['return_rate']*100:.1f}% "
        f"({focus_txt}) | Taxe vente {p['sales_tax']*100:.1f}% | Fraicheur ≤ {p['max_age']:.0f}h | "
        f"Marge ≥ {p['min_margin']:.0f}%. Recettes et focus issus de ao-bin-dumps."
    )

    if not opps:
        st.info("Aucun craft rentable avec ces filtres. Active le focus, augmente la fraicheur, "
                "baisse la marge minimale ou change la liste d'items.")
        return

    df = pd.DataFrame(opps)
    df["icon"] = df["item"].map(ai.icon_url)
    df["focus"] = df["item"].map(lambda i: ai.get_focus(i, meta))
    df["recette"] = df["item"].map(
        lambda i: ", ".join(f"{q}x {r}" for r, q in ai.get_recipe(i, meta).items()))
    df["buy_city"] = df["buy_city"].map(city_badge)
    df["sell_city"] = df["sell_city"].map(city_badge)
    df["buy_strategy"] = df["buy_strategy"].map(strat_badge)
    df["strategy"] = df["strategy"].map(strat_badge)
    df = df.rename(columns={
        "icon": "Icone", "item": "Item crafte", "recette": "Recette", "focus": "Focus",
        "buy_city": "Achat res @", "buy_strategy": "Achat", "total_cost": "Cout total",
        "sell_city": "Vente @", "sell_price": "Prix vente", "strategy": "Vente",
        "profit": "Profit/u", "margin_pct": "Marge %",
    })
    df = df[[
        "Icone", "Item crafte", "Recette", "Focus", "Achat res @", "Cout total", "Achat",
        "Vente @", "Prix vente", "Vente", "Profit/u", "Marge %",
    ]].sort_values("Profit/u", ascending=False)

    craft_marge_max = max(float(df["Marge %"].max()), 1.0)
    st.subheader("Crafts rentables (cliquer sur un en-tete pour trier)")
    st.dataframe(
        df, use_container_width=True, hide_index=True,
        column_config={
            "Icone": icon_col_config(),
            "Focus": st.column_config.NumberColumn(format="%d", help="Focus de craft de base "
                     "(dump). Consomme uniquement si tu craftes avec focus."),
            "Cout total": st.column_config.NumberColumn(format="%d",
                     help="Matieres (apres return rate) + frais craft + cout focus."),
            "Prix vente": st.column_config.NumberColumn(format="%d"),
            "Profit/u": st.column_config.NumberColumn(format="%d"),
            "Marge %": st.column_config.ProgressColumn(
                "Marge %", format="%.1f %%", min_value=0, max_value=craft_marge_max),
        },
    )
    st.download_button(
        "⬇️ Exporter en CSV", data=df.drop(columns="Icone").to_csv(index=False).encode("utf-8"),
        file_name="albion_craft.csv", mime="text/csv",
    )
    st.caption(
        "Focus = points de focus du craft (consomme si tu actives le focus, qui ameliore le "
        "return rate). Cout total = matieres apres return rate + frais. "
        "Achat res = strategie d'achat des ressources ('instant'/'ordre'). "
        "Vente @ = ou revendre l'item crafte."
    )


# --------------------------------------------------------------------------- #
# PAGE + SIDEBAR
# --------------------------------------------------------------------------- #

st.set_page_config(page_title="Albion Trade", page_icon="⚔️", layout="wide")
inject_theme()

with st.sidebar:
    st.header("Parametres communs")
    max_age = st.slider("Fraicheur max (heures)", 1.0, 72.0,
                        float(aa.DEFAULT_MAX_AGE_HOURS), 1.0,
                        help="Tout prix plus vieux que ca est ecarte (regle non negociable).")
    min_margin = st.slider("Marge nette minimale (%)", 0.0, 100.0, 5.0, 1.0)

    st.divider()
    outlier_check = st.checkbox("Garde-fou anti-outlier (historique)", value=True)
    outlier_factor = st.slider("Tolerance anti-outlier (x mediane)", 1.5, 10.0,
                               float(aa.DEFAULT_OUTLIER_FACTOR), 0.5,
                               disabled=not outlier_check)

    st.divider()
    premium = st.checkbox("Compte Premium (taxe vente 4%)", value=True)
    sales_tax = aa.DEFAULT_SALES_TAX if premium else 0.08
    setup_fee = st.number_input("Frais de mise (ordre)", 0.0, 0.10,
                                float(aa.DEFAULT_SETUP_FEE), 0.005, format="%.3f")
    allow_buy_order = st.checkbox("Autoriser l'achat par ordre d'achat", value=True,
                                  help="Acheter via ordre d'achat (buy_price_max + frais), "
                                       "moins cher mais plus lent/incertain.")
    quality = st.selectbox("Qualite (revente)", [1, 2, 3, 4, 5], index=0,
                           format_func=lambda q: {1: "Normal", 2: "Bon", 3: "Exceptionnel",
                                                  4: "Excellent", 5: "Chef d'oeuvre"}[q])
    cities = st.multiselect("Villes a scanner", options=aa.CITIES, default=aa.CITIES)

    st.divider()
    st.markdown("**🚚 Transport** (monture & items)")
    mount_name = st.selectbox("Monture", list(MOUNTS.keys()),
                              index=list(MOUNTS.keys()).index(DEFAULT_MOUNT))
    preset = MOUNTS[mount_name]
    if preset is None:
        speed_factor = st.slider("Facteur de vitesse (<1 = plus rapide)", 0.4, 2.0, 1.0, 0.05)
        capacity_kg = st.number_input("Capacite de charge (kg)", 100, 10000, 1800, 100)
    else:
        speed_factor, capacity_kg = preset["speed"], preset["capacity"]
        st.caption(f"Vitesse x{speed_factor:.2f} · charge {capacity_kg:,} kg")
    fallback_weight = st.number_input("Poids de secours (kg)", 0.1, 50.0,
                                      DEFAULT_UNIT_WEIGHT, 0.1,
                                      help="Sert seulement si un item est absent du dump.")
    items_text = st.text_area("Items transport (un par ligne, vide = defaut)", "", height=90)
    items = [l.strip() for l in items_text.splitlines() if l.strip()] or aa.DEFAULT_ITEMS

    st.divider()
    st.markdown("**🔨 Craft** (focus & recettes)")
    use_focus = st.checkbox("Crafter avec focus", value=False,
                            help="Active le return rate focus (meilleur retour de ressources) ; "
                                 "consomme du focus.")
    return_rate = ac.DEFAULT_FOCUS_RETURN_RATE if use_focus else ac.DEFAULT_RETURN_RATE
    st.caption(f"Return rate applique : {return_rate*100:.1f} %")
    craft_fee = st.number_input("Frais de craft (silver/craft)", 0, 100000, 0, 100)
    focus_cost = st.number_input("Cout du focus (silver/craft)", 0, 100000, 0, 100,
                                 help="Valorisation optionnelle du focus depense.")
    craft_text = st.text_area("Items a crafter (un par ligne, vide = armures+sacs T4-T8)",
                              "", height=90,
                              help="IDs d'items craftables, ex : T6_2H_CLAYMORE. "
                                   "Recette + focus charges depuis ao-bin-dumps.")
    craft_items = ([l.strip() for l in craft_text.splitlines() if l.strip()]
                   or list(ac.DEFAULT_RECIPES.keys()))

    st.divider()
    if st.button("Vider le cache prix", use_container_width=True):
        load_prices.clear()
        load_reference.clear()
        st.rerun()

if not cities:
    st.warning("Selectionne au moins une ville dans la barre laterale.")
    st.stop()

params = {
    "cities": cities, "items": items, "craft_items": craft_items,
    "max_age": max_age, "min_margin": min_margin, "quality": quality,
    "sales_tax": sales_tax, "setup_fee": setup_fee, "allow_buy_order": allow_buy_order,
    "outlier_check": outlier_check, "outlier_factor": outlier_factor,
    "mount_name": mount_name, "speed_factor": speed_factor, "capacity_kg": capacity_kg,
    "fallback_weight": fallback_weight,
    "return_rate": return_rate, "use_focus": use_focus,
    "craft_fee": craft_fee, "focus_cost": focus_cost,
}

meta = load_meta()
tab_transport, tab_craft = st.tabs(["🚚 Transport", "🔨 Craft"])
with tab_transport:
    render_transport(params, meta)
with tab_craft:
    render_craft(params, meta)

st.markdown(
    '<div class="albion-footer"><span class="gem">⚜️</span> Albion Trade · '
    'donnees Albion Online Data Project · serveur EUROPE <span class="gem">⚜️</span></div>',
    unsafe_allow_html=True,
)
