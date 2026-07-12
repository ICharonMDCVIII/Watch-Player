#!/usr/bin/env python3
"""
Badlands Watch
Surveille les lands du serveur Badlands via BlueMap et previent sur Discord
quand un land est declaim (supprime pour inactivite).

Aucune connexion au serveur Minecraft : on lit uniquement les pages web
publiques de la BlueMap, exactement comme un visiteur avec son navigateur.
"""

import json, os, re, html, time, math, urllib.request, urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ------------------------------------------------------------------ CONFIG
WORLDS_PLAYERS = ["mojave", "sierra", "nevada", "spawn"]   # ou passent les joueurs
WORLDS_LANDS   = ["mojave", "sierra", "nevada"]            # le spawn n'a aucun land
URL = "https://{w}.badlands.fr/maps/{w}/live/{f}.json"

HOME_WORLD, HOME_X, HOME_Z = "mojave", 423, -7027          # ta base
MARKERS_EVERY = 3600          # secondes entre 2 scans des lands (1h)
NEW_LAND_ALERTS = False       # True = te prevenir aussi des lands crees

STATE     = Path("data/state.json")
WATCHLIST = Path("data/watchlist.json")
WEBHOOK   = os.environ.get("DISCORD_WEBHOOK", "").strip()

# regle Badlands : heures de jeu -> jours d'inactivite avant suppression
TIERS = [(1, 15), (6, 30), (24, 90), (48, 180), (float("inf"), 365)]

UA = {"User-Agent": "Mozilla/5.0 (badlands-watch)"}
now = datetime.now(timezone.utc)


# ------------------------------------------------------------------ OUTILS
def get(world, kind):
    """Telecharge un json de la BlueMap. Renvoie None si le monde est HS."""
    url = URL.format(w=world, f=kind)
    for essai in range(3):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            if essai == 2:
                print(f"  !! {world}/{kind} injoignable : {e}")
                return None
            time.sleep(3)


def parse_land(detail):
    """Extrait les infos d'un land depuis le HTML du marker."""
    t = re.sub(r"<[^>]+>", "\n", html.unescape(html.unescape(detail)))
    lignes = [l.strip() for l in t.split("\n") if l.strip()]
    d = {"name": lignes[0] if lignes else "?", "owner": None, "level": None,
         "chunks": 0, "balance": 0.0, "players": []}
    for l in lignes:
        m = re.match(r"^(.+?) of (.+?)\.?$", l)
        if m and not any(x in l for x in ("Niveau", "Joueurs", "Solde", "Chunks")):
            d["level"], d["owner"] = m.group(1), m.group(2).rstrip(".")
        if l.startswith("Niveau:"):
            d["level"] = l.split(":", 1)[1].strip()
        if l.startswith("Chunks:"):
            d["chunks"] = int(re.sub(r"[^0-9]", "", l) or 0)
        if l.startswith("Solde:"):
            try:
                d["balance"] = float(re.sub(r"[^0-9,]", "", l).replace(",", ".") or 0)
            except ValueError:
                pass
        if l.startswith("Joueurs"):
            d["players"] = [p.strip() for p in l.split(":", 1)[1].split(",") if p.strip()]
    return d


def tier_days(heures):
    """Palier de suppression en jours, selon les heures de jeu."""
    for seuil, jours in TIERS:
        if heures < seuil:
            return jours
    return 365


def jours_depuis(iso):
    if not iso:
        return None
    return (now - datetime.fromisoformat(iso)).total_seconds() / 86400


def dist(land):
    """Distance en blocs depuis ta base (None si autre monde)."""
    if land.get("world") != HOME_WORLD:
        return None
    return round(math.hypot(land.get("x", 0) - HOME_X, land.get("z", 0) - HOME_Z))


def verdict(land, players):
    """Deduit le palier du proprio au moment de la chute -> ca vaut le coup ou pas."""
    membres = land.get("players") or ([land["owner"]] if land.get("owner") else [])
    vus = [jours_depuis(players.get(p, {}).get("last_seen")) for p in membres]
    vus = [v for v in vus if v is not None]

    # signes exterieurs de richesse : un debutant n'a ni 5 lands ni 500k en banque
    riche = land.get("owner_lands", 0) >= 5 or land.get("balance", 0) >= 500_000

    if not vus:
        age = jours_depuis(STATE_START) or 0
        if age < 20:
            return f"proprio jamais vu (bot lance depuis {age:.0f}j seulement, trop tot pour trancher)", "?"
        if age >= 90:
            return (f"proprio jamais vu en {age:.0f}j de surveillance -> palier long "
                    f"(180j ou 1 an) -> **gros joueur, fonce**"), "+++"
        return f"proprio jamais vu en {age:.0f}j -> palier >= {age:.0f}j, a creuser", "+"

    d = min(vus)  # le membre le plus recemment actif

    if d <= 45 and riche:
        return (f"dernier membre actif il y a seulement {d:.0f}j, mais ce proprio a "
                f"{land.get('owner_lands', '?')} land(s) et {land.get('balance', 0):,.0f} en banque. "
                f"Un debutant tombe pas si vite avec ce profil : c'est probablement une "
                f"**suppression volontaire**, pas un declaim. La base est libre quand meme."), "+"
    if d <= 45:
        return f"dernier membre actif il y a {d:.0f}j -> palier 15 ou 30j -> **petit joueur, laisse tomber**", "--"
    if d <= 120:
        return f"dernier membre actif il y a {d:.0f}j -> palier ~90j -> joueur moyen (6-24h de jeu)", "+"
    if d <= 240:
        return f"dernier membre actif il y a {d:.0f}j -> palier ~180j -> **bon joueur (24-48h de jeu)**", "++"
    return f"dernier membre actif il y a {d:.0f}j -> palier 1 an -> **gros joueur, fonce**", "+++"


def esc(s):
    return re.sub(r"([*_`~|\\])", r"\\\1", str(s))


def envoyer(embeds):
    """Envoie les embeds sur Discord, par paquets de 10."""
    if not WEBHOOK:
        print("  (pas de webhook, rien envoye)")
        for e in embeds:
            print("  --", e["title"])
        return
    for i in range(0, len(embeds), 10):
        data = json.dumps({"embeds": embeds[i:i + 10]}).encode()
        req = urllib.request.Request(
            WEBHOOK, data=data,
            headers={"Content-Type": "application/json", **UA})
        try:
            urllib.request.urlopen(req, timeout=20)
        except urllib.error.HTTPError as e:
            print("  !! Discord :", e.code, e.read()[:200])
        time.sleep(1)


# ------------------------------------------------------------------ ETAT
state = json.loads(STATE.read_text()) if STATE.exists() else \
    {"version": 1, "last_markers": 0, "lands": {}, "players": {}, "started": now.isoformat()}
state.setdefault("started", now.isoformat())
state.setdefault("pinged", [])
STATE_START = state["started"]

lands_old = state["lands"]
players   = state["players"]
alertes   = []

# ------------------------------------------------------- 1. QUI EST EN LIGNE
en_ligne = {}
for w in WORLDS_PLAYERS:
    d = get(w, "players")
    if not d:
        continue
    for p in d.get("players", []):
        if p.get("foreign"):
            continue
        pos = p.get("position", {})
        en_ligne[p["name"]] = {"world": w,
                               "x": round(pos.get("x", 0)),
                               "z": round(pos.get("z", 0))}

for nom, info in en_ligne.items():
    fiche = players.setdefault(nom, {"first_seen": now.isoformat(), "sessions": 0})
    fiche["last_seen"]  = now.isoformat()
    fiche["last_world"] = info["world"]
    fiche["last_pos"]   = [info["x"], info["z"]]
    fiche["sessions"]   = fiche.get("sessions", 0) + 1

print(f"{now:%d/%m %H:%M} | {len(en_ligne)} en ligne | {len(players)} joueurs connus")

# --------------------------------------- 2. WATCHLIST : chute prevue a J-1 ?
if WATCHLIST.exists():
    for nom, info in json.loads(WATCHLIST.read_text()).items():
        try:
            derniere = datetime.fromisoformat(info["last_online"]).replace(tzinfo=timezone.utc)
        except Exception:
            continue
        # une reconnexion observee ecrase la date saisie a la main
        vu = players.get(nom, {}).get("last_seen")
        if vu and datetime.fromisoformat(vu) > derniere:
            derniere = datetime.fromisoformat(vu)

        palier = tier_days(info.get("playtime_hours", 0))
        chute  = derniere + timedelta(days=palier)
        reste  = (chute - now).total_seconds() / 86400
        cle    = f"{nom}:{chute:%Y-%m-%d}"

        if nom in en_ligne and cle in state["pinged"]:
            state["pinged"].remove(cle)   # il est revenu, le timer repart
            alertes.append({
                "title": f"↩️ {esc(nom)} s'est reconnecte",
                "description": f"Son timer repart a zero. Nouvelle chute estimee le "
                               f"**{(now + timedelta(days=palier)):%d/%m/%Y}** "
                               f"(palier {palier}j). Pense a refaire `/l player {nom}`.",
                "color": 0x8899AA})
        elif 0 < reste <= 1 and cle not in state["pinged"]:
            state["pinged"].append(cle)
            terrains = [l for l in lands_old.values()
                        if l.get("owner") == nom or nom in (l.get("players") or [])]
            liste = "\n".join(
                f"• **{esc(l['name'])}** ({l['world']}) — {l['chunks']} chunks, "
                f"`{l['x']} / {l['z']}`" + (f" — {dist(l)} blocs de chez toi" if dist(l) else "")
                for l in sorted(terrains, key=lambda x: -x["chunks"])[:8]) or "aucun land connu"
            alertes.append({
                "title": f"⏰ {esc(nom)} : ses lands tombent dans moins de 24h",
                "description": f"Chute prevue le **{chute:%d/%m/%Y}** "
                               f"(inactif depuis {jours_depuis(derniere.isoformat()):.0f}j, palier {palier}j)\n\n{liste}",
                "color": 0xE67E22})

# ------------------------------------------------- 3. LES LANDS ONT BOUGE ?
scan = time.time() - state.get("last_markers", 0) > MARKERS_EVERY
if scan:
    lands_new, ok = {}, True
    for w in WORLDS_LANDS:
        d = get(w, "markers")
        if not d:
            ok = False
            break
        for cle, m in (d.get("me.angeschossen.lands", {}).get("markers", {})).items():
            lid = cle.split("_")[0]
            if lid in lands_new:
                lands_new[lid]["zones"] += 1
                continue
            info = parse_land(m["detail"])
            pos = m.get("position", {})
            info.update(world=w, x=round(pos.get("x", 0)), z=round(pos.get("z", 0)), zones=1)
            lands_new[lid] = info

    # nb de lands par proprio = proxy du grade (grade eleve = gros joueur = palier long)
    compte = {}
    for l in lands_new.values():
        if l.get("owner"):
            compte[l["owner"]] = compte.get(l["owner"], 0) + 1
    for l in lands_new.values():
        l["owner_lands"] = compte.get(l.get("owner"), 0)

    # un monde HS = on ne touche a rien, sinon on croit que tout a disparu
    if ok and lands_new:
        state["last_markers"] = time.time()
        tombes  = [lands_old[i] for i in lands_old if i not in lands_new]
        nouveaux = [lands_new[i] for i in lands_new if i not in lands_old]

        if len(tombes) > 200:
            print(f"  !! {len(tombes)} disparitions d'un coup : bug probable, on ignore")
            tombes = []

        for l in sorted(tombes, key=lambda x: -x["chunks"]):
            txt, note = verdict(l, players)
            d = dist(l)
            couleur = {"+++": 0x2ECC71, "++": 0x2ECC71, "+": 0xF1C40F}.get(note, 0x95A5A6)
            alertes.append({
                "title": f"💥 LAND TOMBE : {esc(l['name'])}",
                "description": (
                    f"**{esc(l.get('owner') or '?')}** — {l['chunks']} chunks — {l.get('level') or '?'}\n"
                    f"Monde **{l['world']}** — `{l['x']} / {l['z']}`"
                    + (f" — **{d} blocs** de chez toi\n" if d else "\n")
                    + (f"Membres : {esc(', '.join(l['players'][:6]))}\n" if l.get("players") else "")
                    + (f"Solde du land : {l['balance']:,.0f}\n" if l.get("balance") else "")
                    + f"\n{txt}"),
                "color": couleur})

        if NEW_LAND_ALERTS:
            for l in nouveaux[:5]:
                alertes.append({
                    "title": f"🆕 Nouveau land : {esc(l['name'])}",
                    "description": f"{esc(l.get('owner') or '?')} — {l['world']} `{l['x']}/{l['z']}`",
                    "color": 0x3498DB})

        # retrecissement = le proprio unclaim, souvent juste avant de partir
        for lid, ln in lands_new.items():
            lo = lands_old.get(lid)
            if lo and ln["chunks"] < lo["chunks"] * 0.6 and lo["chunks"] >= 20:
                alertes.append({
                    "title": f"📉 {esc(ln['name'])} retrecit",
                    "description": f"{lo['chunks']} → **{ln['chunks']}** chunks. "
                                   f"{esc(ln.get('owner') or '?')} est peut-etre en train de plier bagage.\n"
                                   f"{ln['world']} `{ln['x']}/{ln['z']}`",
                    "color": 0x9B59B6})

        state["lands"] = lands_new
        print(f"  lands : {len(lands_new)} | {len(tombes)} tombes | {len(nouveaux)} nouveaux")

# ------------------------------------------------------------------ ENVOI
if alertes:
    print(f"  -> {len(alertes)} alerte(s)")
    envoyer(alertes)

STATE.parent.mkdir(exist_ok=True)
STATE.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")))
