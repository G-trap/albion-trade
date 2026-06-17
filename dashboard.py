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

from collections import defaultdict

import pandas as pd
import streamlit as st
import plotly.graph_objects as go

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
# Couleur hex par ville (pour les graphiques et pastilles HTML).
CITY_HEX = {
    "Thetford": "#9b59b6", "Fort Sterling": "#e8edf0", "Lymhurst": "#2ecc71",
    "Bridgewatch": "#f1c40f", "Martlock": "#3498db", "Caerleon": "#e74c3c",
    "Black Market": "#6c6c74", "Brecilien": "#a1887f",
}


def city_badge(city):
    """Prefixe une ville par sa pastille de couleur (None -> chaine vide)."""
    if not city:
        return city
    return f"{CITY_COLORS.get(city, '•')} {city}"


def city_dot(city):
    """Pastille HTML coloree + nom (pour les cartes)."""
    c = CITY_HEX.get(city, "#caa42b")
    return f'<span class="cdot" style="background:{c}"></span>{city}'


def compute_score(o, max_profit, max_age):
    """Score /100 : melange profit (50%), marge (35%), fraicheur (15%)."""
    prof = (o["profit"] / max_profit) if max_profit else 0.0
    marg = min(o["margin_pct"] / 100.0, 1.0)
    age = max(o["buy_age_h"], o["sell_age_h"])
    fresh = 1.0 - min(age / max_age, 1.0) if max_age else 1.0
    return round(max(0.0, min(100.0, 100 * (0.5 * prof + 0.35 * marg + 0.15 * fresh))))


def score_tier(score):
    return "good" if score >= 80 else ("mid" if score >= 60 else "low")


def sparkline_svg(values, w=160, h=38):
    """Mini-courbe SVG (verte si tendance haussiere, rouge sinon)."""
    vals = [float(v) for v in values if v]
    if len(vals) < 2:
        return '<div class="oc-nospark">— pas d\'historique —</div>'
    lo, hi = min(vals), max(vals)
    rng = (hi - lo) or 1.0
    n = len(vals)
    color = "#7bd88f" if vals[-1] >= vals[0] else "#d9776f"
    pts = [f"{i/(n-1)*w:.1f},{h-3-(v-lo)/rng*(h-6):.1f}" for i, v in enumerate(vals)]
    line = " ".join(pts)
    area = f"0,{h} {line} {w},{h}"
    return (f'<svg width="100%" height="{h}" viewBox="0 0 {w} {h}" preserveAspectRatio="none">'
            f'<polygon points="{area}" fill="{color}" opacity="0.13"/>'
            f'<polyline points="{line}" fill="none" stroke="{color}" '
            f'stroke-width="2" stroke-linejoin="round"/></svg>')


def history_series(history_rows):
    """{ item_id -> [avg_price...] } agrege par timestamp (pour les sparklines)."""
    by_item = {}
    for r in history_rows:
        iid = r.get("item_id")
        if not iid:
            continue
        agg = by_item.setdefault(iid, defaultdict(list))
        for pt in (r.get("data") or []):
            ap, ts = pt.get("avg_price"), pt.get("timestamp")
            if ap and ts:
                agg[ts].append(ap)
    out = {}
    for iid, agg in by_item.items():
        out[iid] = [sum(v) / len(v) for _, v in sorted(agg.items())]
    return out


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


@st.cache_data(ttl=900, show_spinner="Recuperation de l'historique...")
def load_history(items, cities):
    """Series d'historique brutes (pour reference + sparklines)."""
    return aa.fetch_history(list(items), list(cities))


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

/* Titres de section facon parchemin */
.sec-title { font-family:'Cinzel',serif; color: var(--gold-light); font-size:1.05rem;
  letter-spacing:1.5px; text-transform:uppercase; margin:18px 0 10px 0;
  display:flex; align-items:center; gap:8px; }
.sec-title .bar { flex:1 1 auto; height:1px;
  background:linear-gradient(90deg, rgba(201,162,39,.5), transparent); }

/* Cartes de statistiques (rangee du haut) */
.stat-card { background: linear-gradient(180deg, rgba(38,29,20,.9), rgba(24,18,12,.9));
  border:1px solid rgba(201,162,39,.30); border-radius:12px; padding:14px 16px;
  box-shadow: inset 0 0 22px rgba(201,162,39,.05); height:100%; }
.stat-card .sc-top { display:flex; align-items:center; gap:8px; color:#bcae90;
  font-size:.82rem; text-transform:uppercase; letter-spacing:.6px; }
.stat-card .sc-icon { font-size:1.1rem; }
.stat-card .sc-value { font-family:'Cinzel',serif; color: var(--gold-light);
  font-size:1.7rem; font-weight:700; margin-top:6px; line-height:1.1; }
.stat-card .sc-sub { color:#9a8d70; font-size:.8rem; margin-top:2px; }

/* Cartes d'opportunites (top) */
.opp-card { background: linear-gradient(180deg, rgba(38,29,20,.92), rgba(22,17,11,.92));
  border:1px solid rgba(201,162,39,.30); border-radius:12px; padding:12px 14px; height:100%;
  box-shadow: 0 4px 14px rgba(0,0,0,.4); }
.opp-card .oc-head { display:flex; align-items:center; gap:10px; }
.opp-card .oc-img { width:38px; height:38px; border-radius:7px;
  border:1px solid rgba(201,162,39,.4); background:#0e0b07; }
.opp-card .oc-name { font-family:'Cinzel',serif; color: var(--gold-light); font-weight:600;
  font-size:.95rem; }
.opp-card .oc-route { color:#b8ab8d; font-size:.8rem; margin-top:1px; }
.opp-card .oc-spark { margin:10px 0 8px; }
.opp-card .oc-nospark { color:#6f6450; font-size:.75rem; text-align:center; padding:8px 0; }
.opp-card .oc-stats { display:flex; justify-content:space-between; gap:6px;
  border-top:1px solid rgba(201,162,39,.15); padding-top:8px; }
.opp-card .oc-stats > div { display:flex; flex-direction:column; }
.opp-card .oc-k { color:#9a8d70; font-size:.68rem; text-transform:uppercase; letter-spacing:.4px; }
.opp-card .oc-v { color:#ece3d0; font-family:'Cinzel',serif; font-size:.95rem; }
.opp-card .oc-score { display:flex; justify-content:space-between; align-items:center;
  margin-top:10px; color:#9a8d70; font-size:.75rem; text-transform:uppercase; letter-spacing:.5px; }
.cdot { display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:4px;
  vertical-align:middle; box-shadow:0 0 0 1px rgba(0,0,0,.4); }

/* Badge de score */
.score-badge { font-family:'Cinzel',serif; font-weight:700; padding:2px 9px; border-radius:14px;
  font-size:.82rem; border:1px solid; }
.score-badge.good { color:#9be8a8; border-color:rgba(123,216,143,.5); background:rgba(123,216,143,.12); }
.score-badge.mid  { color:#e6c66a; border-color:rgba(201,162,39,.5); background:rgba(201,162,39,.12); }
.score-badge.low  { color:#d99; border-color:rgba(200,90,80,.5); background:rgba(200,90,80,.12); }

/* Panneau generique (graphiques) */
.panel { background: linear-gradient(180deg, rgba(34,26,18,.85), rgba(21,16,11,.85));
  border:1px solid rgba(201,162,39,.25); border-radius:12px; padding:14px 16px; height:100%; }
.panel .p-title { font-family:'Cinzel',serif; color:#cbbd9b; font-size:.82rem;
  text-transform:uppercase; letter-spacing:.8px; margin-bottom:8px; }
.panel .p-big { font-family:'Cinzel',serif; color: var(--gold-light); font-size:1.9rem; font-weight:700; }
.route-row { display:flex; align-items:center; gap:8px; padding:5px 0;
  border-bottom:1px solid rgba(201,162,39,.12); font-size:.9rem; color:#d8ccaf; }
.route-row .rank { font-family:'Cinzel',serif; color: var(--gold); width:18px; }
.route-row .cnt { margin-left:auto; color:#9a8d70; }
</style>
"""


# Embleme heraldique (ecu + epees croisees discretes + balance de marchand doree),
# SVG inline = sans dependance externe.
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
  <!-- epees croisees, en retrait -->
  <g stroke="#b8ad95" stroke-width="2.4" stroke-linecap="round" opacity="0.55">
    <line x1="27" y1="30" x2="57" y2="64"/><line x1="57" y1="30" x2="27" y2="64"/>
  </g>
  <!-- balance de marchand (or), embleme central -->
  <g stroke="url(#or)" stroke-width="2.4" stroke-linecap="round" fill="none">
    <line x1="42" y1="37" x2="42" y2="65"/>
    <line x1="34" y1="66" x2="50" y2="66"/>
    <line x1="27" y1="42" x2="57" y2="42"/>
    <line x1="27" y1="42" x2="22" y2="50"/><line x1="27" y1="42" x2="32" y2="50"/>
    <line x1="57" y1="42" x2="52" y2="50"/><line x1="57" y1="42" x2="62" y2="50"/>
    <path d="M21 50 Q27 57.5 33 50"/><path d="M51 50 Q57 57.5 63 50"/>
  </g>
  <circle cx="42" cy="36" r="2.7" fill="url(#or)" stroke="#7a611a" stroke-width="1"/>
</svg>
"""


def inject_theme():
    st.markdown(ALBION_CSS, unsafe_allow_html=True)
    st.markdown(
        '<div class="albion-header">'
        '<div class="albion-emblem">' + EMBLEM_SVG + '</div>'
        '<div class="albion-htext">'
        '<div class="title">⚖️ MARCHÉ D\'ALBION</div>'
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

def section_title(text, icon=""):
    st.markdown(f'<div class="sec-title">{icon} {text}<span class="bar"></span></div>',
                unsafe_allow_html=True)


def stat_card(col, icon, label, value, sub):
    col.markdown(
        f'<div class="stat-card"><div class="sc-top"><span class="sc-icon">{icon}</span>'
        f'<span>{label}</span></div><div class="sc-value">{value}</div>'
        f'<div class="sc-sub">{sub}</div></div>', unsafe_allow_html=True)


def opp_card(col, o, series):
    spark = sparkline_svg(series.get(o["item"], []))
    age = max(o["buy_age_h"], o["sell_age_h"])
    tier = score_tier(o["score"])
    col.markdown(
        f'<div class="opp-card"><div class="oc-head">'
        f'<img class="oc-img" src="{ai.icon_url(o["item"])}"/>'
        f'<div><div class="oc-name">{o["item"]}</div>'
        f'<div class="oc-route">{city_dot(o["buy_city"])} → {city_dot(o["sell_city"])}</div>'
        f'</div></div><div class="oc-spark">{spark}</div>'
        f'<div class="oc-stats">'
        f'<div><span class="oc-k">Profit/u</span><span class="oc-v">{o["profit"]:,} 🪙</span></div>'
        f'<div><span class="oc-k">Marge</span><span class="oc-v">{o["margin_pct"]:.0f}%</span></div>'
        f'<div><span class="oc-k">Âge</span><span class="oc-v">{age:.0f}h</span></div></div>'
        f'<div class="oc-score"><span>Score</span>'
        f'<span class="score-badge {tier}">{o["score"]}/100</span></div></div>',
        unsafe_allow_html=True)


def _plotly_layout(fig, height=180):
    fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font_color="#9a8d70", margin=dict(l=0, r=0, t=0, b=0), height=height)
    return fig


def margin_donut(opps):
    hi = sum(1 for o in opps if o["margin_pct"] >= 40)
    mid = sum(1 for o in opps if 20 <= o["margin_pct"] < 40)
    lo = sum(1 for o in opps if o["margin_pct"] < 20)
    fig = go.Figure(go.Pie(labels=["≥ 40 %", "20–40 %", "< 20 %"], values=[hi, mid, lo],
                           hole=.62, sort=False, textinfo="none",
                           marker=dict(colors=["#c9a227", "#d98c3a", "#b5483f"],
                                       line=dict(color="#15110c", width=2))))
    fig.update_layout(showlegend=True, legend=dict(font=dict(color="#cbbd9b", size=11)))
    return _plotly_layout(fig, 180)


def profit_hist(opps):
    fig = go.Figure(go.Histogram(x=[o["profit"] for o in opps], nbinsx=16,
                                 marker=dict(color="#c9a227", line=dict(color="#7a611a", width=.5))))
    fig.update_layout(xaxis=dict(showgrid=False), yaxis=dict(showgrid=False, visible=False),
                      bargap=.08)
    return _plotly_layout(fig, 180)


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

    reference, series = None, {}
    try:
        history_rows = load_history(tuple(items), tuple(cities))
        series = history_series(history_rows)
        if p["outlier_check"]:
            reference = aa.build_reference_prices(history_rows)
    except Exception as e:  # noqa: BLE001
        st.warning(f"Historique indisponible : {e}")

    opps = aa.find_opportunities(
        rows, p["sales_tax"], p["setup_fee"], p["max_age"], p["quality"],
        reference=reference, outlier_factor=p["outlier_factor"],
        allow_buy_order=p["allow_buy_order"],
    )
    opps = [o for o in opps if o["margin_pct"] >= p["min_margin"]
            and o["profit"] >= p["profit_min"]]
    max_profit = max((o["profit"] for o in opps), default=1)
    for o in opps:
        o["score"] = compute_score(o, max_profit, p["max_age"])
    opps = [o for o in opps if o["score"] >= p["score_min"]]
    opps.sort(key=lambda o: o["score"], reverse=True)

    if not opps:
        st.info("Aucune opportunite avec ces filtres. Baisse la marge / le profit / le score "
                "minimum, augmente la fraicheur, ou change la liste d'items.")
        return

    # --- 5 cartes de statistiques ---
    top = opps[0]
    ages = [max(o["buy_age_h"], o["sell_age_h"]) for o in opps]
    fa = min(ages)
    fresh = f"{fa*60:.0f} min" if fa < 1 else f"{fa:.1f} h"
    avg_marge = sum(o["margin_pct"] for o in opps) / len(opps)
    s1, s2, s3, s4, s5 = st.columns(5)
    stat_card(s1, "💰", "Profit potentiel max", f'{top["profit"]:,} 🪙', top["item"])
    stat_card(s2, "🎯", "Opportunites rentables", f"{len(opps)}", f"Sur {len(rows):,} lignes")
    stat_card(s3, "🚚", "Meilleure route", f'{top["buy_city"]} → {top["sell_city"]}',
              f'Marge {top["margin_pct"]:.1f} %')
    stat_card(s4, "🛡️", "Donnees fraiches", fresh, "Age min des prix")
    stat_card(s5, "📈", "Marge moyenne", f"{avg_marge:.1f} %", "Sur les opportunites")

    # --- TOP OPPORTUNITES (cartes) ---
    section_title("Top opportunites", "🏆")
    for col, o in zip(st.columns(5), opps[:5]):
        opp_card(col, o, series)

    # --- Graphiques ---
    section_title("Analyse", "📊")
    g1, g2, g3 = st.columns([1.1, 1.3, 1.1])
    with g1:
        with st.container(border=True):
            st.markdown('<div class="p-title">Repartition des marges</div>', unsafe_allow_html=True)
            st.plotly_chart(margin_donut(opps), use_container_width=True,
                            config={"displayModeBar": False})
    with g2:
        with st.container(border=True):
            st.markdown('<div class="p-title">Distribution des profits</div>', unsafe_allow_html=True)
            st.plotly_chart(profit_hist(opps), use_container_width=True,
                            config={"displayModeBar": False})
    with g3:
        with st.container(border=True):
            st.markdown('<div class="p-title">Meilleures routes</div>', unsafe_allow_html=True)
            routes = defaultdict(lambda: [0, 0])
            for o in opps:
                k = (o["buy_city"], o["sell_city"])
                routes[k][0] += 1
                routes[k][1] += o["profit"]
            top_routes = sorted(routes.items(), key=lambda kv: kv[1][0], reverse=True)[:5]
            html = ""
            for i, ((b, s), (cnt, _)) in enumerate(top_routes, 1):
                html += (f'<div class="route-row"><span class="rank">{i}</span>'
                         f'{city_dot(b)} → {city_dot(s)}<span class="cnt">{cnt}</span></div>')
            st.markdown(html, unsafe_allow_html=True)

    # --- Tableau complet ---
    section_title("Toutes les opportunites", "📜")
    query = st.text_input("Rechercher un item", "", placeholder="🔎 ex : T6_CLOTH",
                          label_visibility="collapsed")
    shown = [o for o in opps if query.strip().lower() in o["item"].lower()] if query else opps

    df = pd.DataFrame(shown)
    df["age_max_h"] = df[["buy_age_h", "sell_age_h"]].max(axis=1)
    df.insert(0, "#", range(1, len(df) + 1))
    df["icon"] = df["item"].map(ai.icon_url)
    df["buy_city"] = df["buy_city"].map(city_badge)
    df["sell_city"] = df["sell_city"].map(city_badge)
    df["buy_strategy"] = df["buy_strategy"].map(strat_badge)
    df["strategy"] = df["strategy"].map(strat_badge)
    df = df.rename(columns={
        "icon": "Icone", "item": "Item", "buy_city": "Achat @", "buy_price": "Prix achat",
        "buy_strategy": "Achat", "sell_city": "Vente @", "sell_price": "Prix vente",
        "strategy": "Vente", "profit": "Profit/u", "margin_pct": "Marge %",
        "age_max_h": "Age max (h)", "score": "Score",
    })
    df = df[["#", "Icone", "Item", "Achat @", "Prix achat", "Achat", "Vente @",
             "Prix vente", "Vente", "Profit/u", "Marge %", "Age max (h)", "Score"]]
    marge_max = max(float(df["Marge %"].max()), 1.0)
    st.dataframe(
        df, use_container_width=True, hide_index=True,
        column_config={
            "#": st.column_config.NumberColumn(width="small"),
            "Icone": icon_col_config(),
            "Prix achat": st.column_config.NumberColumn(format="%d"),
            "Prix vente": st.column_config.NumberColumn(format="%d"),
            "Profit/u": st.column_config.NumberColumn(format="%d"),
            "Marge %": st.column_config.ProgressColumn(
                "Marge %", format="%.1f %%", min_value=0, max_value=marge_max),
            "Age max (h)": st.column_config.NumberColumn(format="%.1f"),
            "Score": st.column_config.NumberColumn(format="%d", help="0-100 : profit, marge, fraicheur."),
        },
    )
    st.download_button(
        "⬇️ Exporter en CSV", data=df.drop(columns="Icone").to_csv(index=False).encode("utf-8"),
        file_name="albion_transport.csv", mime="text/csv",
    )
    st.caption(
        "**Achat / Vente** : ⚡ instant (immediat) ou 📜 ordre (poste, plus lent). "
        "**Score** = profit (50%) + marge (35%) + fraicheur (15%). "
        "AGE = anciennete max de la donnee utilisee."
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

st.set_page_config(page_title="Marché d'Albion", page_icon="⚖️", layout="wide")
inject_theme()

with st.sidebar:
    st.markdown("### ⚙️ Filtres")
    min_margin = st.slider("Marge minimale (%)", 0.0, 100.0, 5.0, 1.0)
    profit_min = st.slider("Profit minimal (silver)", 0, 50000, 0, 500)
    score_min = st.slider("Score minimal", 0, 100, 0, 5)

    st.markdown("### 🛡️ Fiabilite")
    outlier_check = st.checkbox("Garde-fou anti-outlier", value=True)
    outlier_factor = st.slider("Tolerance (x mediane)", 1.5, 10.0,
                               float(aa.DEFAULT_OUTLIER_FACTOR), 0.5,
                               disabled=not outlier_check)
    premium = st.checkbox("Compte Premium (+4 %)", value=True)
    sales_tax = aa.DEFAULT_SALES_TAX if premium else 0.08
    allow_buy_order = st.checkbox("Achat par ordre d'achat", value=True,
                                  help="Acheter via ordre d'achat (buy_price_max + frais), "
                                       "moins cher mais plus lent/incertain.")
    max_age = st.slider("Age max des prix (h)", 1.0, 72.0,
                        float(aa.DEFAULT_MAX_AGE_HOURS), 1.0)
    quality = st.selectbox("Qualite (revente)", [1, 2, 3, 4, 5], index=0,
                           format_func=lambda q: {1: "Normal", 2: "Bon", 3: "Exceptionnel",
                                                  4: "Excellent", 5: "Chef d'oeuvre"}[q])

    st.markdown("### 💰 Taxes & frais")
    setup_fee = st.number_input("Frais de mise (ordre)", 0.0, 0.10,
                                float(aa.DEFAULT_SETUP_FEE), 0.005, format="%.3f")

    st.markdown("### 🏰 Villes a scanner")
    cities = st.multiselect("Villes", options=aa.CITIES, default=aa.CITIES,
                            label_visibility="collapsed")

    with st.expander("🚚 Transport — monture & items"):
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
                                          DEFAULT_UNIT_WEIGHT, 0.1)
        items_text = st.text_area("Items transport (un par ligne, vide = defaut)", "", height=80)
        items = [l.strip() for l in items_text.splitlines() if l.strip()] or aa.DEFAULT_ITEMS

    with st.expander("🔨 Craft — focus & recettes"):
        use_focus = st.checkbox("Crafter avec focus", value=False)
        return_rate = ac.DEFAULT_FOCUS_RETURN_RATE if use_focus else ac.DEFAULT_RETURN_RATE
        st.caption(f"Return rate applique : {return_rate*100:.1f} %")
        craft_fee = st.number_input("Frais de craft (silver/craft)", 0, 100000, 0, 100)
        focus_cost = st.number_input("Cout du focus (silver/craft)", 0, 100000, 0, 100)
        craft_text = st.text_area("Items a crafter (vide = armures+sacs T4-T8)", "", height=80,
                                  help="IDs craftables, ex : T6_2H_CLAYMORE (recette + focus du dump).")
        craft_items = ([l.strip() for l in craft_text.splitlines() if l.strip()]
                       or list(ac.DEFAULT_RECIPES.keys()))

    st.divider()
    if st.button("🔄 Actualiser les donnees", use_container_width=True, type="primary"):
        load_prices.clear()
        load_reference.clear()
        load_history.clear()
        st.rerun()

if not cities:
    st.warning("Selectionne au moins une ville dans la barre laterale.")
    st.stop()

params = {
    "cities": cities, "items": items, "craft_items": craft_items,
    "max_age": max_age, "min_margin": min_margin, "quality": quality,
    "profit_min": profit_min, "score_min": score_min,
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
    '<div class="albion-footer"><span class="gem">⚜️</span> Marché d\'Albion · '
    'données Albion Online Data Project · serveur EUROPE <span class="gem">⚜️</span></div>',
    unsafe_allow_html=True,
)
