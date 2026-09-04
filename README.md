# Board

A self-hosted board for school assignments, personal tasks, and daily/weekly habits.
Four lists, editable from the browser, stored in one SQLite file on your NAS.

No frameworks, no build step, no package manager. The server is Python standard
library only; the frontend is one HTML file.

```
server.py            http.server + sqlite3, ~250 lines
static/index.html    the entire frontend
docker-compose.yml   for TrueNAS
```

## Behavior

**Lists** come in three kinds. *Tasks* lists (School, Personal) have due dates and a
folding Completed drawer. *Daily* and *Weekly* lists are habit trackers with counts
and no drawer.

**The day rolls over at 3:00 AM.** Anything checked off at 12:30 AM counts for the
previous day. Weeks run Monday through Sunday. Daily habits show completions this
week out of 7; weekly habits show completions this month out of the number of
Mondays in that month, so some months read `/5`.

**Everything is editable in place.** List titles, section names, and item text are
click-to-type. Rows drag anywhere within their own list; lists drag across the board.
Deletions are immediate with a seven-second Undo in the toast at the bottom.

## Deploying on TrueNAS

1. Push this repo to GitHub (public, so the clone needs no credentials).
2. Create two datasets: one for the code, one for the database. For example
   `/mnt/pool/apps/board` and `/mnt/pool/appdata/board`.
3. Edit `docker-compose.yml`: your GitHub URL, your dataset paths, your timezone.
4. In the TrueNAS UI: **Apps → Discover Apps → Custom App → Install via YAML**.
   Paste the compose file and deploy.
5. Open `http://<nas>:8420`.

The `fetch` sidecar clones the repo on first run and pulls on every subsequent start,
so **deploying a change is: push to GitHub, then Restart the app in the TrueNAS UI.**
No SSH, no image build, no registry.

### HTTPS and the phone

On the NAS, with Tailscale already running:

```
tailscale serve --bg https / http://localhost:8420
```

That gives you a real certificate on your `.ts.net` name. Browsers only offer
"Add to Home Screen" as a proper app over HTTPS, so this is worth doing.

### Optional token

Leave `BOARD_TOKEN` empty if you trust everything on your tailnet. If you set it,
visit `http://<nas>:8420/?t=YOUR_TOKEN` once on each device; the server stores it in
a year-long cookie. This is a speed bump against stray devices on your LAN, not real
authentication.

## Data

Everything lives in `board.db` in the data directory, as a single JSON document plus
the last 50 versions in a `history` table. Snapshot that dataset and you have
backups. The Export button in the top bar writes the same document to a file, and
Import reads it back.

To recover a previous version by hand:

```
sqlite3 /mnt/pool/appdata/board/board.db \
  "SELECT doc FROM history ORDER BY id DESC LIMIT 1 OFFSET 1;" > rollback.json
```

Then use Import in the UI.

## Multi-device

Every write carries a version number. If your phone tries to save against a version
the laptop already moved past, the server answers 409 and the phone takes the
server's copy, with a toast explaining what happened. Open tabs also poll every 30
seconds, so a change on one device shows up on the other without a reload. Polling
pauses while you are typing or dragging so nothing gets yanked out from under you.

## API

```
GET  /api/state    -> {"version": N, "doc": {...}}
PUT  /api/state    <- {"version": N, "doc": {...}}
                   -> 200 {"version": N+1}  |  409 {"version": M, "doc": {...}}
GET  /api/health   -> {"ok": true}
```

## Running it locally

```
BOARD_DATA=./data TZ=America/New_York python3 server.py
```

Then open http://localhost:8080. Requires Python 3.8 or newer and nothing else.

## Notes

- `TZ` must be set or the 3 AM rollover happens on UTC time, which would flip your
  day over in the early evening.
- The code mount is read-only on purpose.
- The font is whatever your system uses. To ship Inter instead, drop `Inter.woff2`
  into `static/` and uncomment the `@font-face` block at the top of `index.html`.
