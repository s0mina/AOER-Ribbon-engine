# Ribbon Engine v3

A tool that builds **faction-themed award ribbons** for Roblox UGC. Pick a
faction, tick the awards/ribbons/commendations you want, type a name, and
the engine outputs a finished 128×128 PNG you can upload.

> **New here? Skip straight to [INSTALLATION.md](INSTALLATION.md).**
> That file walks you through getting the engine running on Windows, Mac,
> or Linux step by step.

This README explains what the engine *is* and how it works. For deep faction
config (palettes, recolor knobs, schema), see [FACTIONS.md](FACTIONS.md).
For the headless command-line renderer, see [CLI.md](CLI.md).

## What it does, in plain English

1. You pick a **faction** from a dropdown (MDC, SORO, etc.).
2. The sidebar fills in with **only the awards/ribbons/commendations
   that faction is allowed to use**. (A faction can't pick stuff it
   doesn't own — that's enforced by which PNG files are physically on
   disk, not by a JSON list a clever user could edit.)
3. You tick the ribbons you've earned, type your nametape, optionally
   drag medals/gorgets into specific slots.
4. The engine **recolors ribbons on the fly** to the faction's palette
   (so a single source ribbon can serve every faction in their own
   colors), composes everything onto a 128×128 canvas, and saves it as
   a PNG.
5. The exported PNG **remembers what you picked** — paste it back into
   the engine later and it restores every checkbox, the nametape, and
   the active faction.

## Folder layout

```
Ribbon Engine v3/
├── ribbonengine.py          # The main app — what the GUI launcher runs
├── factions.py              # Faction registry + recolor pipeline (don't run directly)
├── renderer.py              # Display-free PIL compositing core (headless-testable)
├── profiles.py              # LayoutProfile — the renderer's view of an Engine Profile
├── updater.py               # Self-update logic (checks Releases, stages, applies)
├── cli.py                   # Headless renderer for scripts/batches
├── test_*.py                # Unit tests (factions, renderer, profiles, updater)
├── run.sh                   # Mac/Linux launcher — `./run.sh` from a terminal
├── requirements.txt         # What pip installs (Pillow + NumPy)
│
├── factions/                # Per-faction config (palette, display name)
│   ├── _global.json         #   Shared settings, default faction
│   ├── AOER.json            #   Hidden, holds corp-wide assets
│   └── <FACTION>.json …     #   One per faction
│
├── assets/                  # The PNG library — the filesystem IS the allowlist
│   ├── AOER/                #   Corp-wide pool (visible under every faction)
│   │   ├── ribbons/         #     Ribbons that get recolored per faction
│   │   │   └── *.png.meta.json  #  Optional per-ribbon recolor overrides
│   │   ├── awards/          #     Medals (never recolored)
│   │   └── commendations/   #     Gorgets, badges, commendations (never recolored)
│   └── <FACTION>/           #   One tree per faction. Same three subfolders.
│       ├── shirttemplate.png  #   Optional: shirt preview shown when this faction is active
│       ├── ribbons/
│       ├── awards/
│       └── commendations/
│
├── Engine Profiles/         # Where stuff goes on the 128×128 canvas
├── templates/               # Optional shirt templates for preview overlay
├── Characters/              # Letter tiles for the nametape
├── loadouts/                # Your saved presets (created on first save)
├── ribbonoutput/            # Where generated/exported PNGs land
└── settings.json            # Your preferences (auto-managed)
```

**Security model.** A faction can only render PNGs that are physically
present under `assets/<FACTION>/`. Editing a JSON file to "unlock"
something does nothing on its own — the file has to exist on disk for
that faction. To restrict a recipient, ship them only the
`assets/<FACTION>/` folders they're cleared for.

## Running the engine

See [INSTALLATION.md](INSTALLATION.md) for the full setup steps. Once
installed, the short version is:

- **Windows:** download the latest release, unzip it, and double-click
  **`AOER-Ribbon-engine.exe`**. No Python, no setup — it just runs.
  → <https://github.com/s0mina/AOER-Ribbon-engine/releases/latest>
- **Mac / Linux:** open a terminal in the folder, type `./run.sh`. The
  first launch installs Pillow + NumPy into a local `.venv/` folder
  (~30 seconds); every launch after that is instant.

> The Windows `.exe` is built automatically from the source by GitHub
> Actions on every release tag, so it always matches the code here.
> Developers who want to run from source on Windows can use the manual
> `python ribbonengine.py` steps in [INSTALLATION.md](INSTALLATION.md).

### Staying up to date (Windows `.exe`)

The packaged Windows build **checks for updates on its own**. On launch it
quietly asks GitHub whether a newer release exists; if one does, it pops up an
**Update Available** dialog showing the new version and its changelog. Click
**Update Now** and the engine downloads the new build, installs it, and
relaunches — no manual unzip, no re-copying folders.

- **Your data is safe.** The updater refreshes only the program files. Your
  `factions/`, `loadouts/`, `Characters/`, `Engine Profiles/`, `assets/`, and
  `settings.json` are explicitly excluded from the copy, so nothing you've
  customized is touched.
- **You're in control.** The dialog has **Later** (ask again next launch),
  **Skip This Version** (don't nag about this one again), and **Release Page**
  (open the download in your browser instead).
- **Check manually any time** via **Help → Check for Updates…**, and see your
  current version under **Help → About**.
- **Turn it off** by setting `"check_updates_on_startup": false` in
  `settings.json`. (Auto-update only runs in the packaged `.exe`; running from
  source is never modified.)

## Using the engine

### Picking ribbons

Two ways:

1. **Checkboxes** in the sidebar — the engine arranges checked ribbons
   automatically into rows.
2. **Ribbon Placement panel** — pick a ribbon from the dropdown, click
   the slot on the visual grid where you want it. Lets you control
   exact position and place duplicates of the same ribbon.

You can use both at the same time; the placement panel takes
precedence for the slots you've manually filled.

### Picking medals

The **Awards** section has two mirrored medal rows, each with up to 3
slots:

- **Award medals (under ribbons)** — up to 3, in fixed slots under the
  **ribbons** (right side).
- **Bonus medals (under nametape)** — up to 3, in fixed slots under the
  **nametape** (left side). Think of these as overflow awards.

The three slots are **fixed positions**, not a centered group: **slot 1 =
left notch, slot 2 = centre, slot 3 = right notch**. A medal always sits
on its own slot's notch and never moves because of what's in the other
slots — so a single medal in slot 2 stays dead-centre, and filling slots 1
and 2 puts them on the left and centre notches (they don't straddle the
middle). The slot count is odd, so the middle slot lines up with the
pocket's centre on the X axis.
- Both dropdowns list the **full medal pool** — any medal can go in any
  slot, in either row. Duplicates are allowed (triple Diamond is legal).
- Both rows share the same **Y anchor** (the `sacks` row) and are simple
  mirror images of each other, so a medal looks identical no matter which
  pocket it sits in.
- **Department badge** (checkbox) — ticking it **replaces the bonus row**
  with a single department badge (Moderation, Logistics, etc.) under the
  nametape. The award row is unaffected.

By default each row auto-spaces its medals (spacing = widest medal + 1px)
so nothing stacks. To override the geometry, use **Tools → Profile
Editor → Medal offsets**:

- **Award/Bonus 1–3 X/Y** — a per-slot nudge *added* to that slot's
  auto-computed position. `0` means "leave it where auto put it".
- **Award/Bonus row spacing** — forces an exact center-to-center spacing
  for that row. `0` means "auto" (width-based, the default).

> **Adding a medal:** drop the PNG into `assets/<FACTION>/awards/` (or
> `assets/AOER/awards/` for corp-wide) and it shows up in **both** medal
> dropdowns automatically — no profile edit needed. There's no longer a
> separate "bonus names" list; any medal can be placed in either row from
> the dropdowns. Department badges come from the special-badges pool, not
> `awards/`.

### Recolor controls (Settings… button)

Three toggles control how much of a ribbon gets recolored:

| Toggle | What it does |
|---|---|
| **Recolor border** | Forces the outer 1-pixel ring to the faction's `border_color`. **On by default** — gives every faction a consistent border. |
| **Recolor stripe** | Re-tints bright interior pixels to `stripe_color`. Off by default. |
| **Recolor base** | Re-tints dark interior pixels to `base_color`. Off by default. |

The defaults (border only) keep the designer's interior artwork intact
and just lock the outer ring. Flip stripe and base on if you want the
engine to fully normalize a ribbon to the faction's three palette
colors.

### Shirt preview overlay

Tick **Show shirt preview** to drop your ribbon stack onto a mockup of the
uniform shirt so you can judge placement in context. This is preview-only — it
never changes the exported 128×128 PNG.

The shirt **follows the faction automatically.** When you switch factions the
engine looks inside `assets/<FACTION>/` for a shirt image and swaps the preview
to it:

1. It prefers a file named exactly **`shirttemplate.png`**.
2. Failing that, it uses the **first top-level PNG** in that faction's folder,
   so a faction can ship any single shirt image under any name.
3. If the faction has no top-level PNG, the preview keeps the profile/ANRO
   default — so the stock install is unchanged.

To give a faction its own uniform, drop a `shirttemplate.png` into
`assets/<FACTION>/` (alongside the `ribbons/`, `awards/`, `commendations/`
folders — **not** inside them). It ships in the release zip automatically. You
can also still pick any template manually from the **Shirt preview** dropdown,
or drop extra PNGs into the `templates/` folder to make them selectable for
every faction.

> The ribbons are cropped onto the chest using the profile's `front_crop_box` /
> `template_size`. If a faction's shirt is framed differently from the reference
> and the ribbons land off the pocket, tune those values in the Engine Profile.

### Themes

Settings → Theme picks between **XP** (classic), **Light**, and **Dark**.
Restart for a fully consistent look after switching.

### Loadouts (saved presets)

Click **Loadouts…** next to the faction dropdown. Save your current
ribbon/medal/nametape setup under a name, load it back later. Loadouts
live in `loadouts/<NAME>.json` and you can copy them between machines.

A loadout remembers **everything** you set up:

- Every checked ribbon
- Every manually-placed ribbon (the right-side grid)
- Award medals + bonus medals
- Department badge (if any)
- Nametape text
- Active faction
- Which **profile** you were using (so loading a 4-rows-per-row loadout
  on the default profile won't silently drop the extras — see below)

**Favorites.** Each row in the Loadouts dialog has a ☆ button. Click it
to mark that loadout as a favorite (★). Favorites sort to the top of
the list — handy once you've got 20+ saved presets and want your
day-to-day setups one click away. Click ★ again to un-favorite. The
star list is saved to `settings.json` so it survives restarts.

**Searching.** The Search box filters the list as you type — case-
insensitive substring match against the loadout name.

**Profile mismatch warning.** If you load a PNG (or loadout) that was
saved under a different profile than you're currently using, the engine
pops up:

> This image was made under profile `full_stack`.
> You're currently on `default`.
> Switch to the source profile? (No = load anyway, Cancel = abort)

- **Yes** — swap profiles first, then load. Safest option; nothing gets
  dropped.
- **No** — load anyway. Any manual slots that don't exist in your
  current profile's grid get silently ignored. The checkbox ribbons,
  nametape, and medals still come through.
- **Cancel** — back out, nothing changes.

### Sharing loadouts with other people

Click **Export Loadout** (next to Loadouts…). The engine asks for a
loadout name, then writes a normal-looking PNG into `ribbonoutput/`
with the loadout data baked into its metadata. Send that PNG to
someone else.

**To install a received loadout:** drop the PNG straight into your
`loadouts/` folder. That's it — open the **Loadouts…** dialog and it
shows up in the list alongside your saved ones, with a thumbnail
rendered from the PNG itself. Click **Load** to apply it. No prompts,
no import step, no extra JSON file.

**Important:** send the PNG as a **file attachment**, not pasted
inline. Discord and most chat apps re-encode inline images and that
strips the metadata. As an attachment it survives.

### Diffing loadouts (or comparing against what's on screen)

Click **Diff…** and the file picker opens. Two ways to use it:

- **Pick 1 PNG** → the engine compares that PNG against **what's
  currently on your screen** (your unsaved setup). The "(current)"
  column is rendered live from memory, not from a saved file.
- **Pick 2 or more PNGs** (Shift- or Ctrl-click) → the engine compares
  them against each other in a matrix.

The dialog has two tabs:

- **Ribbons** — a matrix listing every ribbon, award, bonus, and
  department badge across all the loadouts. `X` means present, `.`
  means absent, `xN` means N copies stacked in different slots.
  Totals at the bottom give a quick sanity check.
- **Side-by-side** — the actual saved PNG pixels, scaled 2×, displayed
  next to each other with the filename underneath. If "(current)" is
  in the list, that column renders fresh from your live state.

Useful for promotion boards ("what did I earn since last month?") and
for debugging ("does this exported PNG actually match what I built?").

### Output

Click **Generate Image** and the engine writes:

```
[YOUR_NAMETAPE]_[YEAR-MONTH-DAY-HOUR-MINUTE-SECOND].png
```

…to the `ribbonoutput/` folder inside the project (created automatically on first save). Spaces in your nametape become `_`; any
Windows-forbidden characters are stripped. Example output filename:

```
[John_IA]_[2026-05-17-23-17-56].png
```

The PNG has **invisible metadata baked in** that records every checkbox
and the faction. **Drag the exported PNG back into the engine** (or use
"Paste Image from Clipboard") and it'll restore the whole state.

## Adding new things

### A new ribbon for an existing faction

Drop the PNG into `assets/<FACTION>/ribbons/`. Done. No JSON edit
needed — the engine reads the folder.

### A new corp-wide ribbon/award/commendation (visible to everyone)

Drop the PNG into `assets/AOER/<type>/`, where `<type>` is `ribbons`,
`awards`, or `commendations`.

### A new faction

Create `factions/<KEY>.json` (see [FACTIONS.md](FACTIONS.md) for the
schema), then create `assets/<KEY>/ribbons/`, `…/awards/`, and
`…/commendations/` and populate them. The faction shows up in the
dropdown on next launch. Optionally drop a `shirttemplate.png` directly in
`assets/<KEY>/` to give the faction its own shirt-preview overlay (see
[Shirt preview overlay](#shirt-preview-overlay)).

### A different canvas layout (medal positions, ribbon rows)

Edit `Engine Profiles/default.json`. Coordinates, row capacities, and
medal pocket positions all live there. Or use the in-app **Profile
Editor** (Tools → Profile Editor…) — it gives you a spinbox for every
number and a Raw JSON tab as an escape hatch. Click **New…** next to
the Profile dropdown to make a fresh profile by duplicating the current
one.

**Ribbon rows tab.** Ribbons fill from the bottom row upward. Two things
control the rack shape:

- **Right-align start row** — rows *below* this number are centered; this
  row and everything above it are right-justified. That's the classic
  military ribbon-rack look (centered bottom rows, right-aligned top rows).
- **Row 1–8 capacity** — how many ribbons fit in each row, set
  individually. Row 1 is the bottom row. Rows past 8 reuse row 8's
  capacity, so you never run out of rows.

In the JSON these are the `ribbon_rows` keys `right_start_row` and
`row_1_capacity` … `row_8_capacity`. Older profiles that used the previous
`centered_row_capacity` / `first_right_row_capacity` /
`subsequent_right_row_capacity` keys still load unchanged — any row you
don't set explicitly falls back to those old values.

### Moving or renaming a ribbon between factions

Tools → **Move/Rename Ribbon…** opens a small dialog with four
fields:

- **From faction** — where the ribbon currently lives.
- **Ribbon** — pick from a dropdown of every PNG in that
  faction's `ribbons/` folder.
- **To faction** — where you want it to end up. (Set this the same as
  "From faction" if you just want to rename in place.)
- **New name (optional)** — leave blank to keep the existing name, or
  type a new stem (no `.png` extension).

Click Move. The engine:

1. Validates that the new name doesn't contain any
   Windows-forbidden characters (`<>:"/\\|?*`).
2. Asks before overwriting if the target name is already taken.
3. Moves both the PNG and its sidecar (`.meta.json`, if it exists).
4. Reloads the asset tree so the change shows up immediately.

Use this when you're reorganizing — e.g. a ribbon you originally put
under MDC should actually be corp-wide (move it to `AOER`), or you
fixed a typo in the filename.

### Telling one ribbon to skip recolor (per-ribbon override)

The Settings recolor toggles are **global** — they apply to every
ribbon for the current faction. If you have one bespoke ribbon
(a heritage award, a hand-painted commemorative, etc.) that should
**never** be recolored regardless of the toggles, drop a sidecar file
next to it.

`assets/SORO/ribbons/Heritage.png` → `assets/SORO/ribbons/Heritage.png.meta.json`

The sidecar is a tiny JSON file. The simplest version says "leave this
alone":

```json
{ "no_recolor": true }
```

You can also override **just some** of the recolor regions for that
one ribbon (the global Settings toggles still apply to every other
ribbon):

```json
{
  "recolor": {
    "border": true,
    "stripe": false,
    "base":   false
  }
}
```

Missing keys fall back to whatever's in Settings, so you only need to
list the ones you want to force. If you list `no_recolor: true`, the
whole `recolor` block is ignored — the ribbon renders untouched.

The sidecar travels with the PNG when you use Move/Rename Ribbon, so
you don't lose the override.

## How recoloring works (technical)

Every non-transparent pixel of a ribbon PNG is classified:

1. Within `border_thickness` (default 1) of the edge → **border** region.
2. Otherwise, compute brightness `(R*299 + G*587 + B*114) / 1000`.
   - ≥ 140 → **stripe** region.
   - < 140 → **base** region.
3. For each region, if its Settings toggle is on, replace the pixel
   with the faction's color (border = flat replace; stripe/base =
   luminance-preserving tint).
4. The alpha (transparency) channel is always preserved exactly.

Classification reads only **position** and **brightness** — never hue.
A designer who ships a ribbon with weird source colors still renders
correctly, because the engine is the source of truth on color.

Only files under `assets/<FACTION>/ribbons/` get recolored. Anything
under `awards/` or `commendations/` renders in its original art.

A ribbon's `.meta.json` sidecar (see the section above) can opt that
single ribbon out of recolor or override the per-region toggles
without touching anyone else's setup.

Performance: the recolor uses NumPy when available (~50× faster than
the pure-Python fallback) and caches results so toggling stuff in the
GUI feels instant. The cache is per-faction; switching factions
invalidates the loadout thumbnail cache so the dialog repaints in the
new palette.

## Crash safety

Every file the engine writes — exported PNGs, loadout JSONs,
`settings.json`, profile JSONs — is written **atomically**. The engine
writes to `<path>.tmp` first, then renames it on top of the real file.
If your computer crashes or the power goes out halfway through a save,
the previous file is left intact rather than half-written. You should
never see a corrupted `settings.json` after a power loss.

## Asset validation

The engine runs a quick scan on startup and prints warnings to the
terminal if it finds:

- A faction's JSON lists a ribbon, but the PNG isn't on disk.
- A PNG's filename has a Windows-forbidden character (`<>:"/\\|?*`)
  — those break extraction and save on Windows.
- **Duplicate content** — two PNGs in different faction trees that are
  byte-identical. These usually mean someone copied a ribbon between
  factions and it'll drift over time (one gets updated, the other
  doesn't). The warning tells you exactly which paths collide.

You can also run the validator from the command line — see
[CLI.md](CLI.md). It's safe to run in CI: it exits with code `1` if
there are any warnings and `0` if everything's clean.

## Distributing to a faction recipient

To hand out the engine to one faction (e.g. just SORO):

1. Copy the whole project folder.
2. In `factions/`, keep `_global.json`, `AOER.json`, and `SORO.json`.
   Delete everything else.
3. In `assets/`, keep `AOER/` and `SORO/`. Delete everything else.
4. Ship the resulting folder.

The recipient physically cannot render ribbons for any other faction
because the files aren't on their machine.

## Tests

```
python -m unittest discover -p "test_*.py"
```

Should print `OK` after the full suite (faction recolor, the headless
renderer, layout profiles, and the self-updater). These are display-free,
so they run on a headless box and in CI — a red test blocks the `.exe`
build and release. If they don't pass, something is wrong with the install
— check [INSTALLATION.md](INSTALLATION.md).

## Keyboard shortcuts

These work anywhere in the main window:

| Shortcut | What it does |
|---|---|
| **Ctrl + S** | Generate Image (save final PNG to `ribbonoutput/`). |
| **Ctrl + E** | Export Loadout (save a shareable tagged PNG). |
| **Ctrl + D** | Open the Diff dialog. |
| **Ctrl + Z** | Undo. |
| **Ctrl + Y** *or* **Ctrl + Shift + Z** | Redo. |
| **Ctrl + Esc** | Clear everything (nametape, ribbons, medals, manual slots). |
| **Right-click on a manual slot** | Remove the ribbon in that slot. |
| **Drag a PNG onto the window** | Import it (needs `tkinterdnd2` installed — optional). |

Undo/redo covers ribbons, medals, the nametape, manual placements,
and offsets. Switching factions or profiles is **not** part of undo
history — those are treated as deliberate, not transient edits.

## Troubleshooting

**The preview disappears after I click Generate Image.** Fixed in a
recent update. If you're still seeing it, you're on an old build —
pull the latest `ribbonengine.py`.

**My loadout loaded but my manual-placed ribbons are missing.** The
loadout was saved under a different Engine Profile. Look for the
"Profile mismatch" prompt — if it didn't appear, the loadout JSON
predates the profile-aware schema. Open the JSON, add
`"profile": "<name>"`, and reload.

**The Move/Rename Ribbon dialog says "Windows-illegal characters."**
Windows forbids these in filenames: `< > : " / \ | ? *`. Even if
you're on Mac or Linux, the engine still blocks them because most
recipients are on Windows and the file would fail to extract there.
Pick a name without those characters.

**Drag-and-drop doesn't work.** The optional `tkinterdnd2` package
isn't installed. Activate your `.venv` and run
`pip install tkinterdnd2`. The engine works fine without it — only
drag-drop is disabled.

**Discord stripped the metadata from my loadout PNG.** Discord
re-encodes inline image previews. Send the PNG as a **file
attachment** (the "+" → "Upload a File" button, or drag into the
upload area before sending). As an attachment the bytes pass through
untouched and the metadata survives.

**The Loadouts dialog shows weird-colored thumbnails after I
switched factions.** Close and reopen the dialog. The thumbnail
cache is cleared on faction switch, but only the next dialog open
triggers the repaint.

**The validator flagged a duplicate but I want to keep both
copies.** Two PNGs being byte-identical is almost always a mistake
— if they really are supposed to be the same image, put it in
`assets/AOER/ribbons/` (corp-wide) and delete the per-faction
copies. AOER's content is visible to every faction and recolored
into their palette automatically. If you genuinely need two copies
with the same bytes (e.g. you'll edit one of them later but haven't
yet), the warning is harmless and can be ignored.

**Recent files list got too long / contains files I deleted.** The
list auto-trims to 8 entries and skips paths that no longer exist on
disk at startup. To wipe it entirely, close the engine, edit
`settings.json`, set `"recent_files": []`, and relaunch.

## Credits

- Original ANRO Ribbon Engine: djsogge (v1), potatergaming (v2 + v2.5)
- Nametape character tiles: lolcraft101_owner
- v3 faction system, recolor pipeline, per-faction asset tree:
  lolcraft101_owner, AOER

## Known limitations

- Clipboard paste works out of the box on Windows and macOS. On Linux
  it additionally needs `wl-paste` (Wayland) or `xclip` (X11) — see the
  Linux section of [INSTALLATION.md](INSTALLATION.md).
- Theme switching repaints most widgets but a restart is needed for a
  fully consistent look.
- Faction directory names are case-sensitive on Linux. Use the same
  casing everywhere (`MDC`, not `mdc`) to stay portable.
