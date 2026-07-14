#!/usr/bin/env python3
"""
Badlands Watch
Surveille les lands du serveur Badlands via BlueMap et previent sur Discord
quand un land est declaim ou retrecit.

Aucune connexion au serveur Minecraft : on lit uniquement les pages web
publiques de la BlueMap, exactement comme un visiteur avec son navigateur.

Le script tourne EN BOUCLE pendant LOOP_MINUTES et releve les joueurs toutes
les POLL_SECONDS. Le cron GitHub etant peu fiable (des heures de trou entre
deux runs), c'est la boucle qui assure la couverture, pas le cron.
"""

import json, os, re, html, time, math, subprocess, urllib.request, urllib.error, urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ------------------------------------------------------------------ CONFIG
WORLDS_PLAYERS = ["mojave", "sierra", "nevada", "spawn"]   # ou passent les joueurs
WORLDS_LANDS   = ["mojave", "sierra", "nevada"]            # le spawn n'a aucun land
URL = "https://{w}.badlands.fr/maps/{w}/live/{f}.json"

HOME_WORLD, HOME_X, HOME_Z = "mojave", 423, -7027          # ta base
MARKERS_EVERY = 3600          # secondes entre 2 scans des lands (1h)
NEW_LAND_ALERTS = False       # True = te prevenir aussi des lands crees

LOOP_MINUTES = int(os.environ.get("LOOP_MINUTES", "0"))    # 0 = un seul passage
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "120"))

STATE     = Path("data/state.json")
WATCHLIST = Path("data/watchlist.json")
WEBHOOK   = os.environ.get("DISCORD_WEBHOOK", "").strip()

# regle Badlands : heures de jeu -> jours d'inactivite avant suppression
TIERS = [(1, 15), (6, 30), (24, 90), (48, 180), (float("inf"), 365)]

UA = {"User-Agent": "Mozilla/5.0 (badlands-watch)"}


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
                print(f"  !! {world}/{kind} injoignable : {e}", flush=True)
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
    for seuil, jours in TIERS:
        if heures < seuil:
            return jours
    return 365


def jours_depuis(iso, now):
    if not iso:
        return None
    return (now - datetime.fromisoformat(iso)).total_seconds() / 86400


def dist(land):
    """Distance en blocs depuis ta base (None si autre monde)."""
    if land.get("world") != HOME_WORLD:
        return None
    return round(math.hypot(land.get("x", 0) - HOME_X, land.get("z", 0) - HOME_Z))


def verdict(land, players, debut, now):
    """Renvoie (note, phrase). La note pilote la couleur et la pastille."""
    membres = land.get("players") or ([land["owner"]] if land.get("owner") else [])
    vus = [jours_depuis(players.get(p, {}).get("last_seen"), now) for p in membres]
    vus = [v for v in vus if v is not None]

    riche = land.get("owner_lands", 0) >= 5 or land.get("balance", 0) >= 500000

    if not vus:
        age = jours_depuis(debut, now) or 0
        if age < 20:
            return "?", (f"Aucun membre croisé, mais je ne surveille que depuis {age:.0f} jour(s). "
                         f"Trop tôt pour juger.")
        if age >= 90:
            return "+++", (f"Aucun membre croisé en {age:.0f} jours de surveillance. "
                           f"Palier long (180 j ou 1 an) → des joueurs installés.")
        return "+", f"Aucun membre croisé en {age:.0f} jours. Palier d'au moins {age:.0f} jours."

    d = min(vus)
    if d <= 45 and riche:
        return "+", (f"Dernier membre actif il y a {d:.0f} jours seulement, mais ce land a "
                     f"{nb(land.get('balance', 0))} en banque : pas un profil de débutant. "
                     f"Sans doute une suppression volontaire.")
    if d <= 45:
        return "--", (f"Dernier membre actif il y a {d:.0f} jours → palier 15 ou 30 jours "
                      f"→ moins de 6 h de jeu.")
    if d <= 120:
        return "+", f"Dernier membre actif il y a {d:.0f} jours → palier 90 jours → 6 à 24 h de jeu."
    if d <= 240:
        return "++", f"Dernier membre actif il y a {d:.0f} jours → palier 180 jours → 24 à 48 h de jeu."
    return "+++", f"Dernier membre actif il y a {d:.0f} jours → palier 1 an → gros joueur."


NOTES = {
    "+++": ("\U0001F7E2", 0x2ECC71, "Fonce"),
    "++":  ("\U0001F7E2", 0x27AE60, "Ça vaut le coup"),
    "+":   ("\U0001F7E1", 0xF39C12, "À voir"),
    "--":  ("\u26AA", 0x95A5A6, "Petit joueur"),
    "?":   ("\u2754", 0x7F8C8D, "Trop tôt pour dire"),
}


def nb(n):
    """1234 -> '1 234'"""
    return f"{int(n):,}".replace(",", "\u202f")


def tete(pseudo):
    """La tete du skin, via Minotar. Steve par defaut si le pseudo est inconnu."""
    if not pseudo:
        return None
    return "https://minotar.net/helm/" + urllib.parse.quote(str(pseudo)) + "/64.png"


def carte(land):
    """Lien direct vers l'endroit sur la BlueMap."""
    w = land.get("world")
    return (f"https://{w}.badlands.fr/#{w}:{land.get('x', 0)}:0:{land.get('z', 0)}"
            f":300:0:0:0:0:perspective")


def champ(nom, valeur, inline=True):
    return {"name": nom, "value": valeur or "\u2014", "inline": inline}


def esc(s):
    return re.sub(r"([*_`~|\\])", r"\\\1", str(s))


def envoyer(embeds):
    """Envoie les embeds sur Discord, par paquets de 10."""
    if not WEBHOOK:
        for e in embeds:
            print("  [pas de webhook] " + e["title"], flush=True)
        return
    for i in range(0, len(embeds), 10):
        data = json.dumps({"embeds": embeds[i:i + 10]}).encode()
        req = urllib.request.Request(
            WEBHOOK, data=data,
            headers={"Content-Type": "application/json", **UA})
        try:
            urllib.request.urlopen(req, timeout=20)
        except urllib.error.HTTPError as e:
            print("  !! Discord :", e.code, e.read()[:200], flush=True)
        time.sleep(1)



def sauver(state, msg="state"):
    """Ecrit le state sur le disque ET le pousse sur GitHub tout de suite.
    Sans ca, un run qui plante perd tout et le suivant re-annonce les memes chutes."""
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")))
    if not os.environ.get("GITHUB_ACTIONS"):
        return
    try:
        run = lambda *a: subprocess.run(a, capture_output=True, timeout=90)
        run("git", "config", "user.name", "badlands-watch")
        run("git", "config", "user.email", "bot@users.noreply.github.com")
        run("git", "add", "data/state.json")
        if subprocess.run(["git", "diff", "--quiet", "--cached"]).returncode == 0:
            return                      # rien de neuf
        run("git", "commit", "-m", msg)
        run("git", "pull", "--rebase", "--autostash", "origin",
            os.environ.get("GITHUB_REF_NAME", "main"))
        p = run("git", "push")
        if p.returncode:
            print("  !! push refuse :", p.stderr.decode()[:150], flush=True)
    except Exception as e:
        print("  !! git :", e, flush=True)


# ------------------------------------------------------------------ CYCLE
def cycle(state):
    """Un passage complet : qui est en ligne, les lands ont-ils bouge, alertes."""
    now = datetime.now(timezone.utc)
    players   = state["players"]
    lands_old = state["lands"]
    debut     = state["started"]
    alertes   = []

    # --- 1. qui est en ligne
    en_ligne = {}
    for w in WORLDS_PLAYERS:
        d = get(w, "players")
        if not d:
            continue
        for p in d.get("players", []):
            if p.get("foreign"):
                continue
            pos = p.get("position", {})
            en_ligne[p["name"]] = {"world": w, "x": round(pos.get("x", 0)),
                                   "z": round(pos.get("z", 0))}

    for nom, info in en_ligne.items():
        f = players.setdefault(nom, {"first_seen": now.isoformat(), "sessions": 0})
        f["last_seen"]  = now.isoformat()
        f["last_world"] = info["world"]
        f["last_pos"]   = [info["x"], info["z"]]
        f["sessions"]   = f.get("sessions", 0) + 1

    print(f"{now:%d/%m %H:%M} | {len(en_ligne):>3} en ligne | "
          f"{len(players):>4} joueurs connus", flush=True)

    # --- 2. watchlist : chute prevue dans moins de 24h ?
    if WATCHLIST.exists():
        try:
            wl = json.loads(WATCHLIST.read_text())
        except Exception:
            wl = {}
        for nom, info in wl.items():
            if nom.startswith("_") or not isinstance(info, dict):
                continue
            try:
                derniere = datetime.fromisoformat(info["last_online"]).replace(tzinfo=timezone.utc)
            except Exception:
                continue
            vu = players.get(nom, {}).get("last_seen")
            if vu and datetime.fromisoformat(vu) > derniere:
                derniere = datetime.fromisoformat(vu)

            palier = tier_days(info.get("playtime_hours", 0))
            chute  = derniere + timedelta(days=palier)
            reste  = (chute - now).total_seconds() / 86400
            cle    = f"{nom}:{chute:%Y-%m-%d}"

            if nom in en_ligne and cle in state["pinged"]:
                state["pinged"].remove(cle)
                e = {
                    "title": f"\u21A9\uFE0F  {esc(nom)} est revenu",
                    "description": (f"Son compteur repart de zéro. Prochaine échéance vers le "
                                    f"**{(now + timedelta(days=palier)):%d/%m/%Y}** "
                                    f"(palier {palier} jours).\n"
                                    f"Refais un `/l player {nom}` pour la date exacte."),
                    "color": 0x7F8C8D,
                    "timestamp": now.isoformat(),
                    "footer": {"text": "\u26AA Ce land ne tombera pas"},
                }
                if nom:
                    e["thumbnail"] = {"url": tete(nom)}
                alertes.append(e)
            elif 0 < reste <= 1 and cle not in state["pinged"]:
                state["pinged"].append(cle)
                terrains = [l for l in lands_old.values()
                            if nom in (l.get("players") or []) or l.get("owner") == nom]
                terrains.sort(key=lambda x: -x["chunks"])
                e = {
                    "title": f"\u23F0  {esc(nom)} : ça tombe dans moins de 24 h",
                    "description": (f"Chute prévue le **{chute:%d/%m/%Y}** \u00b7 "
                                    f"palier {palier} jours."),
                    "color": 0xE67E22,
                    "fields": [],
                    "timestamp": now.isoformat(),
                    "footer": {"text": "\U0001F7E2 Sois sur place"},
                    "thumbnail": {"url": tete(nom)},
                }
                for l in terrains[:6]:
                    d = dist(l)
                    e["fields"].append(champ(
                        f"{l['name']} \u00b7 {nb(l['chunks'])} chunks",
                        f"{l['world'].capitalize()} \u00b7 `{l['x']} / {l['z']}`"
                        + (f" \u00b7 {nb(d)} blocs" if d else "")
                        + f"\n[Voir sur la carte]({carte(l)})", False))
                if not terrains:
                    e["fields"].append(champ("Lands", "aucun land connu à son nom", False))
                alertes.append(e)

    # --- 3. les lands ont-ils bouge ?
    if time.time() - state.get("last_markers", 0) > MARKERS_EVERY:
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
                info.update(world=w, x=round(pos.get("x", 0)),
                            z=round(pos.get("z", 0)), zones=1)
                lands_new[lid] = info

        if ok and lands_new:
            compte = {}
            for l in lands_new.values():
                if l.get("owner"):
                    compte[l["owner"]] = compte.get(l["owner"], 0) + 1
            for l in lands_new.values():
                l["owner_lands"] = compte.get(l.get("owner"), 0)

            state["last_markers"] = time.time()
            tombes   = [(i, lands_old[i]) for i in lands_old if i not in lands_new]
            nouveaux = [(i, lands_new[i]) for i in lands_new if i not in lands_old]

            if len(tombes) > 200:
                print(f"  !! {len(tombes)} disparitions d'un coup : anormal, on ignore", flush=True)
                tombes = []

            for lid, l in sorted(tombes, key=lambda x: -x[1]["chunks"]):
                note, phrase = verdict(l, players, debut, now)
                pastille, couleur, etiquette = NOTES[note]
                d = dist(l)
                membres = l.get("players") or []
                e = {
                    "title": f"\U0001F4A5  {l['name']} est tombé",
                    "description": f"**{nb(l['chunks'])} chunks** \u00b7 {l.get('level') or '?'}"
                                   f"\n[Voir sur la carte]({carte(l)})",
                    "color": couleur,
                    "fields": [
                        champ("Propriétaire", esc(l.get("owner") or "inconnu")),
                        champ("Monde", l["world"].capitalize()),
                        champ("Distance", f"{nb(d)} blocs" if d else "autre monde"),
                        champ("Coordonnées", f"`{l['x']} / {l['z']}`", False),
                    ],
                    "timestamp": now.isoformat(),
                    "footer": {"text": f"{pastille} {etiquette}"},
                    "_id": f"chute:{lid}",
                }
                if l.get("owner"):
                    e["thumbnail"] = {"url": tete(l["owner"])}
                if len(membres) > 1:
                    liste = ", ".join(esc(m) for m in membres[:5])
                    if len(membres) > 5:
                        liste += f" +{len(membres) - 5}"
                    e["fields"].append(champ(f"Membres ({len(membres)})", liste, False))
                if l.get("balance"):
                    e["fields"].append(champ("Banque du land", nb(l["balance"])))
                e["fields"].append(champ("Verdict", phrase, False))
                alertes.append(e)

            if NEW_LAND_ALERTS:
                for lid, l in nouveaux[:5]:
                    alertes.append({
                        "title": f"\U0001F195  {l['name']}",
                        "description": f"Nouveau land \u00b7 [Voir sur la carte]({carte(l)})",
                        "color": 0x3498DB,
                        "fields": [
                            champ("Propriétaire", esc(l.get("owner") or "?")),
                            champ("Monde", l["world"].capitalize()),
                            champ("Coordonnées", f"`{l['x']} / {l['z']}`"),
                        ],
                        "timestamp": now.isoformat(),
                        "_id": f"neuf:{lid}",
                    })

            # retrecissement : le plugin rase les chunks vides, garde le bati
            for lid, ln in lands_new.items():
                lo = lands_old.get(lid)
                if lo and ln["chunks"] < lo["chunks"] * 0.6 and lo["chunks"] >= 20:
                    perdu = lo["chunks"] - ln["chunks"]
                    d = dist(ln)
                    membres = ln.get("players") or []
                    gros = ln["chunks"] >= 30
                    e = {
                        "title": f"\U0001F4C9  {ln['name']} a été rasé",
                        "description": (f"**{nb(lo['chunks'])} \u2192 {nb(ln['chunks'])} chunks** "
                                        f"(\u2212{nb(perdu)})\n"
                                        f"Le terrain nu a sauté, il ne reste que le bâti."
                                        f"\n[Voir sur la carte]({carte(ln)})"),
                        "color": 0x2ECC71 if gros else 0x9B59B6,
                        "fields": [
                            champ("Propriétaire", esc(ln.get("owner") or "inconnu")),
                            champ("Monde", ln["world"].capitalize()),
                            champ("Distance", f"{nb(d)} blocs" if d else "autre monde"),
                            champ("Coordonnées", f"`{ln['x']} / {ln['z']}`", False),
                        ],
                        "timestamp": now.isoformat(),
                        "footer": {"text": "\U0001F7E2 Reste du bâti, ça vaut le détour"
                                           if gros else "\u26AA Petite construction"},
                        "_id": f"rase:{lid}:{ln['chunks']}",
                    }
                    if ln.get("owner"):
                        e["thumbnail"] = {"url": tete(ln["owner"])}
                    if membres:
                        liste = ", ".join(esc(m) for m in membres[:5])
                        if len(membres) > 5:
                            liste += f" +{len(membres) - 5}"
                        e["fields"].append(champ(f"Membres restants ({len(membres)})", liste, False))
                        e["fields"].append(champ(
                            "À faire", "Tape `/l player` sur chaque membre : le dernier valide "
                                       "tient le land, sa date de chute est celle du land.", False))
                    alertes.append(e)

            state["lands"] = lands_new
            print(f"  lands : {len(lands_new)} | {len(tombes)} tombes | "
                  f"{len(nouveaux)} nouveaux", flush=True)

    # garde-fou : meme si un commit foire, on ne redit jamais deux fois la meme chose
    deja = state.setdefault("annonces", [])
    a_envoyer = []
    for a in alertes:
        cle = a.pop("_id", None)
        if cle and cle in deja:
            continue                      # deja annonce, on saute
        if cle:
            deja.append(cle)
        a_envoyer.append(a)
    del deja[:-1000]                      # on garde les 1000 dernieres

    if a_envoyer:
        print(f"  -> {len(a_envoyer)} alerte(s)", flush=True)
        envoyer(a_envoyer)
        sauver(state, f"alertes {now:%d/%m %H:%M}")   # on grave immediatement


# ------------------------------------------------------------------ MAIN
def main():
    now = datetime.now(timezone.utc)
    state = json.loads(STATE.read_text()) if STATE.exists() else \
        {"version": 1, "last_markers": 0, "lands": {}, "players": {}}
    state.setdefault("started", now.isoformat())
    state.setdefault("pinged", [])
    state.setdefault("players", {})
    state.setdefault("lands", {})

    fin   = time.time() + LOOP_MINUTES * 60
    tours = 0

    while True:
        tours += 1
        try:
            cycle(state)
        except Exception as e:
            print(f"  !! erreur dans le cycle : {e}", flush=True)

        # sur le disque a chaque tour, sur GitHub toutes les 20 min
        STATE.parent.mkdir(exist_ok=True)
        STATE.write_text(json.dumps(state, ensure_ascii=False, separators=(",", ":")))
        if tours % max(1, 1200 // POLL_SECONDS) == 0:
            sauver(state, f"state {datetime.now(timezone.utc):%d/%m %H:%M}")

        if time.time() + POLL_SECONDS >= fin:
            break
        time.sleep(POLL_SECONDS)

    sauver(state, f"state {datetime.now(timezone.utc):%d/%m %H:%M}")
    print(f"\n{tours} releve(s) sur ce run.", flush=True)


if __name__ == "__main__":
    main()
