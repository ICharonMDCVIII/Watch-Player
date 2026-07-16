import os
import sys
import json
import urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

PARIS = ZoneInfo("Europe/Paris")

# Créneaux de vote (heure de Paris).
# True  = les 2 votes dispo (1h30 + 3h)
# False = seulement le vote 1h30
SLOTS = {
    (9, 30): True,
    (11, 0): False,
    (12, 30): True,
    (14, 0): False,
    (15, 30): True,
    (17, 0): False,
    (18, 30): True,
    (20, 0): False,
    (21, 30): True,
    (23, 0): False,
    (0, 30): True,
}

TOLERANCE_MIN = 20   # fenêtre après le créneau pour absorber le retard du cron

# Optionnel : colle tes liens de vote (clic direct dans Discord)
LINK_90 = ""    # lien du vote 1h30
LINK_180 = ""   # lien du vote 3h

WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL")
if not WEBHOOK:
    sys.exit("DISCORD_WEBHOOK_URL manquant (secret non configuré).")
USER_ID = os.environ.get("DISCORD_USER_ID", "").strip()


def find_due_slot(now):
    for (h, m), both in SLOTS.items():
        slot = now.replace(hour=h, minute=m, second=0, microsecond=0)
        diff = (now - slot).total_seconds() / 60
        if 0 <= diff <= TOLERANCE_MIN:
            return both
    return None


def main():
    now = datetime.now(PARIS)
    due = find_due_slot(now)
    if due is None:
        print(f"{now:%H:%M} — aucun créneau, rien à faire.")
        return

    ping = f"<@{USER_ID}> " if USER_ID else ""
    if due:
        msg = f"{ping}🗳️ **Les 2 votes sont dispo** (1h30 + 3h) — go !"
        links = " ".join(l for l in [LINK_90, LINK_180] if l)
    else:
        msg = f"{ping}🗳️ **Vote 1h30 dispo** — go !"
        links = LINK_90
    if links:
        msg += f"\n{links}"

    payload = {"content": msg, "allowed_mentions": {"parse": ["users"]}}
    req = urllib.request.Request(
        WEBHOOK,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as r:
        print(f"{now:%H:%M} — envoyé ({r.status}).")


if __name__ == "__main__":
    main()
