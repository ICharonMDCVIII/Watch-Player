# Badlands Watch

Surveille les 1750 lands du serveur Badlands et prévient sur Discord dès qu'un land tombe.

Aucune connexion au serveur Minecraft, aucun mod, aucun compte alt : le bot lit uniquement
les pages publiques de la BlueMap, exactement comme un visiteur avec son navigateur.
Zéro risque de ban.

---

## Ce qu'il fait

**Toutes les 5 minutes** — il regarde qui est en ligne sur les 4 mondes et note la date.
Au fil des semaines il se construit la liste de qui joue encore et qui a disparu.

**Toutes les heures** — il compare la liste des lands à celle d'avant.
Un land qui a disparu = il vient de tomber → ping Discord avec le nom, le proprio, les coords,
la taille, et un verdict sur l'intérêt du butin.

**Le verdict** — le bot regarde depuis quand il n'a plus vu le proprio en ligne :

| Dernière fois vu | Palier déduit | Verdict |
|---|---|---|
| il y a ~20-30 j | 15 ou 30 j (moins de 6h de jeu) | petit joueur, laisse tomber |
| il y a ~90 j | 6-24h de jeu | moyen |
| il y a ~180 j | 24-48h de jeu | bon joueur |
| il y a 300 j+ / jamais vu | plus de 48h de jeu | **fonce** |

Plus le bot tourne longtemps, plus le verdict est fiable.

---

## Installation (15 min, gratuit)

### 1. Le webhook Discord
Sur ton serveur Discord : **Paramètres du salon → Intégrations → Webhooks → Nouveau webhook**.
Copie l'URL.

### 2. Le repo GitHub
Crée un repo **public** (obligatoire : les minutes GitHub Actions ne sont illimitées que sur
les repos publics, sinon tu exploses le quota gratuit en 4 jours).
Envoie-y les fichiers de ce dossier en gardant l'arborescence.

### 3. Le secret
Sur le repo : **Settings → Secrets and variables → Actions → New repository secret**
- Nom : `DISCORD_WEBHOOK`
- Valeur : l'URL du webhook

### 4. Lancer
Onglet **Actions** → autorise les workflows → **Badlands Watch** → **Run workflow**.
Le premier run ne dira rien (normal, il compare à la baseline du 12/07). Ensuite ça tourne seul.

---

## Régler le tir

Tout est en haut de `watch.py` :

```python
HOME_WORLD, HOME_X, HOME_Z = "mojave", 423, -7027   # ta base, pour la distance
MARKERS_EVERY = 3600      # 1h entre 2 scans des lands
NEW_LAND_ALERTS = False   # True = te prévenir aussi des lands créés
```

## Le J-1 sur tes cibles

Le bot ne connaît pas le temps de jeu des joueurs, donc il ne peut pas prédire une chute
tout seul. Pour tes vraies cibles, tape `/l player <pseudo>` en jeu **une seule fois** et
colle le résultat dans `data/watchlist.json` :

```json
{
  "Crack_Fit": { "last_online": "2026-02-17", "playtime_hours": 1373 }
}
```

`playtime_hours` = le temps de jeu converti en heures (57 jours = 57 × 24 = 1368).
Le bot calcule le palier, la date de chute, et te ping **24h avant**.
Si le mec se reconnecte entre-temps, le bot te le dit et remet le compteur à zéro.

---

## Bon à savoir

- Le cron GitHub est parfois en retard (5 min annoncées, 15-20 min en heure de pointe).
  Sans conséquence : un land qui tombe reste tombé.
- Si un monde est injoignable, le bot ne touche à rien plutôt que de croire à 600 disparitions.
- Un land peut aussi disparaître parce que le proprio l'a supprimé lui-même. Dans les deux cas
  la base est libre, mais le verdict le signale quand le profil du joueur est incohérent.
- Le `data/state.json` est commité à chaque run : au bout de quelques mois tu auras
  l'historique complet du serveur (qui a quitté, quand, quel land est tombé quand).
