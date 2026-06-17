#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Albion Online - Metadonnees des items (depuis ao-bin-dumps)
===========================================================

Recupere, pour chaque item, depuis items.json du depot github.com/ao-data/ao-bin-dumps :
  - le poids (@weight, kg) -> potentiel portable d'une monture
  - le focus de craft (@craftingfocus) -> cout en focus
  - la recette (craftresource : ressources + quantites)

Le dump fait ~17 Mo : on le telecharge une fois, on en extrait un mapping compact
{ uniquename -> {weight, focus, components} } mis en cache disque (item_meta.json) avec TTL.

Usage :
    import albion_items
    meta = albion_items.load_meta()
    albion_items.get_weight("T4_CLOTH", meta)            # 0.51
    albion_items.get_focus("T4_HEAD_CLOTH_SET1", meta)   # 429
    albion_items.get_recipe("T4_2H_CLAYMORE", meta)      # {'T4_METALBAR': 20, 'T4_LEATHER': 12}

Icones : https://render.albiononline.com/v1/item/{item_id}.png
"""

import json
import os
from datetime import datetime, timezone

import albion_arbitrage as aa  # reutilise le GET gzip + parse JSON

ITEMS_DUMP_URL = "https://raw.githubusercontent.com/ao-data/ao-bin-dumps/master/items.json"
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "item_meta.json")
CACHE_TTL_HOURS = 24 * 7  # le dump bouge rarement : 7 jours

ICON_URL = "https://render.albiononline.com/v1/item/{item_id}.png"


def icon_url(item_id):
    """URL de l'icone de rendu officielle Albion pour un item."""
    return ICON_URL.format(item_id=item_id)


# --------------------------------------------------------------------------- #
# EXTRACTION
# --------------------------------------------------------------------------- #

def _parse_meta(dump):
    """Extrait { uniquename -> {weight, focus, components} } du dump items.json."""
    items = dump.get("items", {})
    meta = {}
    for category, value in items.items():
        if category.startswith("@"):  # attributs xml du noeud racine
            continue
        entries = value if isinstance(value, list) else [value]
        for it in entries:
            if not isinstance(it, dict):
                continue
            name = it.get("@uniquename")
            if not name:
                continue
            entry = meta.setdefault(name, {"weight": None, "focus": None, "components": {}})

            w = it.get("@weight")
            if w is not None:
                try:
                    entry["weight"] = float(w)
                except (TypeError, ValueError):
                    pass

            cr = it.get("craftingrequirements")
            if cr:
                c = cr[0] if isinstance(cr, list) else cr
                if isinstance(c, dict):
                    foc = c.get("@craftingfocus")
                    if foc is not None:
                        try:
                            entry["focus"] = int(foc)
                        except (TypeError, ValueError):
                            pass
                    res = c.get("craftresource")
                    if res is not None:
                        res_list = res if isinstance(res, list) else [res]
                        comps = {}
                        for r in res_list:
                            if isinstance(r, dict) and r.get("@uniquename"):
                                try:
                                    comps[r["@uniquename"]] = int(r.get("@count", 1))
                                except (TypeError, ValueError):
                                    pass
                        if comps:
                            entry["components"] = comps
    return meta


# --------------------------------------------------------------------------- #
# CACHE DISQUE
# --------------------------------------------------------------------------- #

def _cache_is_fresh(path, ttl_hours):
    if not os.path.exists(path):
        return False
    age_h = (datetime.now(timezone.utc).timestamp() - os.path.getmtime(path)) / 3600.0
    return age_h <= ttl_hours


def load_meta(cache_path=CACHE_FILE, ttl_hours=CACHE_TTL_HOURS, force=False):
    """
    Renvoie { uniquename -> {weight, focus, components} }. Sert depuis le cache disque
    s'il est frais, sinon telecharge le dump, construit le mapping et le met en cache.
    """
    if not force and _cache_is_fresh(cache_path, ttl_hours):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("meta"):
                return data["meta"]
        except (OSError, ValueError):
            pass  # cache corrompu -> on retelecharge

    dump = aa._http_get_json(ITEMS_DUMP_URL)
    meta = _parse_meta(dump)
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({"fetched_at": datetime.now(timezone.utc).isoformat(),
                       "count": len(meta), "meta": meta}, f)
    except OSError:
        pass  # pas de cache disque : on renvoie quand meme le mapping en memoire
    return meta


# --------------------------------------------------------------------------- #
# ACCES
# --------------------------------------------------------------------------- #

def _entry(item_id, meta):
    """Entree meta de l'item, en gerant les ids enchantes (T5_CLOTH@2 -> base)."""
    if not item_id:
        return None
    e = meta.get(item_id)
    if e is not None:
        return e
    return meta.get(item_id.split("@", 1)[0])


def get_weight(item_id, meta, default=None):
    """Poids (kg) de l'item, ou `default` si inconnu."""
    e = _entry(item_id, meta)
    if e and e.get("weight") is not None:
        return e["weight"]
    return default


def get_focus(item_id, meta, default=None):
    """Focus de craft de base de l'item, ou `default` si inconnu/non craftable."""
    e = _entry(item_id, meta)
    if e and e.get("focus") is not None:
        return e["focus"]
    return default


def get_recipe(item_id, meta):
    """Recette { ressource -> quantite } de l'item, ou {} si non craftable."""
    e = _entry(item_id, meta)
    if e and e.get("components"):
        return dict(e["components"])
    return {}


# --------------------------------------------------------------------------- #
# NOMS LOCALISES (formatted/items.json)
# --------------------------------------------------------------------------- #

NAMES_DUMP_URL = ("https://raw.githubusercontent.com/ao-data/ao-bin-dumps/"
                  "master/formatted/items.json")
NAMES_CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "item_names.json")


def _parse_names(data, lang="FR-FR", fallback="EN-US"):
    """{ uniquename -> nom localise } (FR-FR, sinon EN-US)."""
    names = {}
    for o in data:
        if not isinstance(o, dict):
            continue
        u = o.get("UniqueName")
        ln = o.get("LocalizedNames") or {}
        if u and ln:
            name = ln.get(lang) or ln.get(fallback)
            if name:
                names[u] = name
    return names


def load_names(cache_path=NAMES_CACHE_FILE, ttl_hours=CACHE_TTL_HOURS, force=False):
    """
    { uniquename -> nom FR } depuis formatted/items.json (gros fichier ~24 Mo).
    Sert depuis le cache disque s'il est frais, sinon telecharge et met en cache.
    """
    if not force and _cache_is_fresh(cache_path, ttl_hours):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("names"):
                return data["names"]
        except (OSError, ValueError):
            pass

    raw = aa._http_get_json(NAMES_DUMP_URL)
    names = _parse_names(raw)
    try:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({"fetched_at": datetime.now(timezone.utc).isoformat(),
                       "count": len(names), "names": names}, f, ensure_ascii=False)
    except OSError:
        pass
    return names


def get_name(item_id, names, default=None):
    """Nom FR de l'item ; gere les ids enchantes ; fallback sur l'id si inconnu."""
    if not item_id:
        return default
    n = names.get(item_id)
    if n:
        return n
    base = item_id.split("@", 1)[0]
    return names.get(base, default if default is not None else item_id)


if __name__ == "__main__":
    m = load_meta(force=True)
    print(f"{len(m)} items charges. Exemples :")
    for ex in ("T4_CLOTH", "T4_HEAD_CLOTH_SET1", "T6_BAG", "T4_2H_CLAYMORE"):
        print(f"  {ex:22} poids={get_weight(ex, m)} kg  focus={get_focus(ex, m)}  "
              f"recette={get_recipe(ex, m)}")
