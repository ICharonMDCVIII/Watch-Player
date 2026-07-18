# Land Watch

Surveille les ~1750 lands du serveur (3 mondes) et prévient sur Discord
dès qu'un land tombe ou se fait ronger.

Aucune connexion au serveur Minecraft, aucun mod, aucun compte alt : le bot lit
uniquement les pages publiques de la BlueMap, exactement comme un visiteur avec
son navigateur. Zéro risque de ban.

Le tout tourne gratuitement sur GitHub Actions, 24h/24. Le PC peut être éteint.

---

## Ce qu'il fait

**En continu (toutes les ~2 min)** — il regarde qui est en ligne sur les 4 mondes
(les différents mondes) et note la date de dernière connexion de chacun.
Au fil des jours il se construit la liste de qui joue encore et qui a disparu.

**Toutes les heures** — il compare la liste des lands à celle d'avant, et réagit à
trois événements :

- **Un land a disparu** → il vient de tomber. Ping Discord : nom, proprio, monde,
  coords, taille, distance de chez toi, skin du proprio, lien direct vers la carte,
  et un verdict sur l'intérêt du butin.
- **Un land a rétréci** → le plugin a rasé les chunks vides et gardé le bâti.
  Ce qui reste = la vraie construction. Plus il reste de chunks, plus il y a à looter.
- **Un membre a disparu d'un land** → le plugin l'a retiré pour inactivité. Le bot
  le note : ça lui apprend le profil d'un joueur même sans l'avoir croisé en ligne.

**Le verdict** — le bot déduit le palier d'inactivité du proprio pour dire si ça vaut
le déplacement. La règle du serveur : plus un joueur a d'heures de jeu, plus son land
met de temps à tomber.

| Dernière fois vu | Palier déduit | Pastille |
|---|---|---|
| il y a ~20-30 j | 15 ou 30 j (moins de 6h de jeu) | ⚪ petit joueur, laisse tomber |
| il y a ~90 j | 6-24h de jeu | 🟡 moyen |
| il y a ~180 j | 24-48h de jeu | 🟢 bon joueur |
| il y a 300 j+ / jamais vu | plus de 48h de jeu | 🟢 fonce |

**Limite honnête** : le bot ne voit un joueur que s'il est en ligne au moment d'un
scan. Il croise presque tout le monde au fil du temps, mais un joueur ultra-occasionnel
peut rester "jamais vu" longtemps → verdict "trop tôt pour juger". La chute de son
land, elle, est toujours détectée.

---

## Anti-doublon

Chaque alerte a un identifiant unique (`chute:<id>`, `rase:<id>:<chunks>`, etc.)
mémorisé dans `data/state.json`. Un même événement n'est jamais annoncé deux fois,
même si un run plante et que le suivant repart d'un état un peu ancien.

---

## Comment ça tourne sur GitHub (à lire quand la liste des runs fait peur)

Le workflow lance un run qui **boucle pendant ~4h40** (`LOOP_MINUTES`), en relevant
les joueurs toutes les 2 min. Un cron tente d'en relancer un régulièrement.

Le `concurrency` fait qu'il n'y a **jamais deux runs en même temps** : le nouveau
attend que l'actuel finisse. Résultat, dans la liste des runs :

- **1 rond jaune (In progress)** = le run qui tourne. Normal, il doit y en avoir un.
- **1 rond gris "Pending"** = le suivant qui attend son tour. Normal.
- **Ronds gris "!" (annulés)** = des crons en trop, virés de la file. **Pas des erreurs.**
- **Rond rouge (X)** = un run qui a dépassé le temps max. Sans gravité (l'état est
  sauvé en cours de route), mais si ça revient souvent, baisser `LOOP_MINUTES`.

Ce qui doit t'alerter : **du rouge SANS aucun jaune ni pending**. Là, la chaîne est
cassée, il faut relancer à la main (Actions → Run workflow).

---

## Réglages

Tout est en haut de `watch.py` :

```python
HOME_WORLD, HOME_X, HOME_Z = "monde", 0, 0   # ta base, pour la distance
MARKERS_EVERY = 3600      # 1h entre 2 scans des lands (le gros fichier)
NEW_LAND_ALERTS = False   # True = te prévenir aussi des lands créés
```

Et dans `.github/workflows/watch.yml` :

```yaml
LOOP_MINUTES: "280"   # durée d'un run (min). Sous le timeout pour finir proprement.
POLL_SECONDS: "120"   # intervalle entre deux relevés de joueurs
```

---

## Le J-1 sur tes cibles (optionnel)

Le bot ne connaît pas le temps de jeu exact des joueurs, donc il ne prédit pas une
date de chute tout seul. Pour une cible précise, tape `/l player <pseudo>` en jeu
**une seule fois** et colle le résultat dans `data/watchlist.json` :

```json
{
  "Crack_Fit": { "last_online": "2026-02-17", "playtime_hours": 1373 }
}
```

`playtime_hours` = temps de jeu en heures (57 jours = 57 × 24 = 1368).
Le bot calcule le palier, la date de chute, et te ping **24h avant**. Si le mec se
reconnecte entre-temps, il te le dit et remet le compteur à zéro.

---

## Bon à savoir

- Le cron GitHub est irrégulier (de quelques minutes à quelques heures entre deux
  déclenchements). Sans conséquence grâce au chaînage : un land qui tombe reste tombé.
- Si un monde est injoignable, le bot ne touche à rien plutôt que de croire à des
  centaines de disparitions.
- Un land peut disparaître parce que le proprio l'a supprimé lui-même. Dans les deux
  cas la base est libre ; le verdict le signale quand le profil du joueur est incohérent
  (ex. land riche mais "petit joueur").
- Le `data/state.json` est commité en continu : au bout de quelques mois tu auras
  l'historique complet du serveur (qui a quitté, quand, quel land est tombé quand).
- Serveur en offline mode : le skin du proprio ne s'affiche pas toujours (carré gris),
  c'est normal, ça dépend du pseudo.
