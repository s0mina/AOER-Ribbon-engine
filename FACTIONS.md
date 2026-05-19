# Faction System

This file explains how factions work, how to add one, and how the
recolor pipeline turns one ribbon image into a different-colored ribbon
for every faction.

> **Just want to run the engine?** See [INSTALLATION.md](INSTALLATION.md).
> **Want to know what the GUI does?** See [README.md](README.md).

## How it fits together

A **faction** is defined by two things:

1. A JSON file at `factions/<KEY>.json` that describes the faction's
   palette (border / stripe / base colors) and display name.
2. A directory at `assets/<KEY>/` that holds the PNGs that faction is
   allowed to render — split into `ribbons/`, `awards/`, and
   `commendations/`.

**Both must exist.** The JSON without the directory means a faction
shows up in the dropdown but has nothing to render. The directory
without the JSON means PNGs exist but the engine doesn't know they
belong to a faction.

## Adding a new faction

Say you want to add **NES** (just an example). Two steps:

### Step 1 — Create the JSON

Make a file `factions/NES.json`:

```json
{
  "key": "NES",
  "display_name": "Northern Engineering Service",
  "color_palette": {
    "base_color": "#001a4d",
    "stripe_color": "#ff6b00",
    "border_color": "#ffffff"
  }
}
```

Field reference:

| Field | Required? | What it does |
|---|---|---|
| `key` | yes | Short ID. Must match the directory name under `assets/`. Convention: also matches the filename. |
| `display_name` | yes | What shows up in the faction dropdown. |
| `color_palette.base_color` | yes | Hex color used to re-tint **dark interior pixels** of a ribbon (when "Recolor base" is on). |
| `color_palette.stripe_color` | yes | Hex color for **bright interior pixels** (when "Recolor stripe" is on). |
| `color_palette.border_color` | yes | Hex color for the **outer 1-pixel ring** of every ribbon (when "Recolor border" is on — which it is by default). |
| `hidden` | no | If `true`, the faction is omitted from the dropdown but its assets become **corp-wide** (visible under every active faction). Used for AOER. |
| `no_recolor` | no | List of asset names to skip recoloring on, even if they live in `ribbons/`. For ribbons whose art shouldn't be tinted. |
| `descriptions` | no | `{ "Asset Name": "tooltip text" }` map shown in the hover preview. |
| `engine_profile` | no | Name of an Engine Profile to switch to when this faction is selected. |

### Step 2 — Create the asset folders

```
assets/NES/
├── ribbons/        # drop *.png files in here
├── awards/         # *.png medal art
└── commendations/  # *.png gorgets/badges/commendations
```

You can leave any of the three empty if NES doesn't have assets of
that type. Just create the folder so the engine knows it exists.

### That's it

Restart the engine. NES appears in the dropdown. The sidebar shows
exactly the PNGs you dropped into the three subfolders, plus whatever
is in `assets/AOER/` (the corp-wide pool — see below).

## How "allowed assets" work

There is **no JSON allowlist** for assets. The filesystem is the
authority. The engine builds the sidebar by scanning, in order:

1. `assets/<ACTIVE_FACTION>/ribbons/` (+ `awards/`, `commendations/`)
2. `assets/<EACH_HIDDEN_FACTION>/…` (i.e. `assets/AOER/…`)

Names from step 1 win if the same filename exists in both — so a
faction can locally override a corp-wide asset by dropping a same-named
PNG into its own folder.

### Why this design

If the JSON were the allowlist, someone could edit it and "unlock"
ribbons they shouldn't have. By making the file's *physical existence*
the gate, you control access by controlling who gets the files. To
restrict a recipient to one faction:

1. Delete `assets/<EVERY_OTHER_FACTION>/` from their copy.
2. Delete `factions/<EVERY_OTHER_FACTION>.json` from their copy.

They literally cannot render what isn't on their disk.

## The corp-wide pool (AOER)

`factions/AOER.json` has `"hidden": true`. This means:

- AOER **never** shows up in the user-facing faction dropdown.
- AOER's assets under `assets/AOER/` are **visible to every faction**.
- A ribbon in `assets/AOER/ribbons/` gets recolored to whichever
  faction is currently active (so the same source PNG can render in
  10 different palettes).

Use AOER for shared assets — generic ribbons every faction earns,
common medals, etc.

## `_global.json`

This file holds settings that aren't tied to one specific faction:

```json
{
  "default_faction": "MDC",
  "shared_assets": [],
  "recolor": {
    "border_thickness": 1,
    "mode": "tint"
  }
}
```

| Field | What it does |
|---|---|
| `default_faction` | Which faction is active on first launch (before the user has switched). Must be the `key` of an actual faction file. |
| `shared_assets` | Legacy field — kept for backward compatibility but no longer used. Asset visibility now comes from filesystem. |
| `recolor.border_thickness` | How many pixels at the edge of a ribbon are treated as "border" (default 1). |
| `recolor.mode` | Reserved for future use. Currently always `"tint"`. |

## The recolor pipeline

Implemented in `factions.py::recolor_ribbon`. For every non-transparent
pixel of a ribbon PNG:

1. **Is it within `border_thickness` of the edge?** → border region.
2. Otherwise compute brightness as `(R*299 + G*587 + B*114) / 1000`.
   - ≥ 140 → **stripe** region.
   - < 140 → **base** region.
3. For each region, if the matching toggle in Settings is on, replace
   the pixel:
   - **border** → flat replace with `border_color`.
   - **stripe** → luminance-preserving tint toward `stripe_color`
     (highlights stay bright, shadows stay dark, but the hue shifts).
   - **base** → same as stripe but with `base_color`.
4. If the toggle is off, the source pixel passes through unchanged.
5. Alpha (transparency) is always preserved exactly.

**Classification reads position and brightness only — never hue.** This
means a designer can ship a ribbon in *any* source colors and the
engine still classifies and recolors it correctly. The brightness
threshold matters, the hex codes don't.

### Why border-only by default

Designers often want creative freedom on the inside of a ribbon
(stripes, symbols, gradients) but a consistent corp-wide border. The
default — `border` on, `stripe`/`base` off — locks the border but
leaves the interior alone. Users can flip stripe/base on in Settings
if they want a full color rewrite.

### NumPy vs pure Python

If NumPy is installed (it is by default — it's in `requirements.txt`),
the recolor runs as a vectorized array operation (~50× faster than the
per-pixel loop). If NumPy is missing, it falls back to a pure-Python
loop that still works, just slower.

### Cache

`FactionRecolorCache` memoizes results keyed on
`(asset_path, faction_key, options_token)`. Toggling Settings changes
the options token, which produces a new cache key — so stale entries
aren't reused.

The GUI also keeps a separate cache of loadout **thumbnails** (the
little previews in the Loadouts dialog). That cache is cleared
automatically whenever the user switches factions, so thumbnails
repaint in the new palette next time the dialog opens.

## Per-ribbon recolor overrides (`.meta.json` sidecars)

The Settings toggles (`border`, `stripe`, `base`) are **global** —
they apply to every ribbon currently on screen. The `no_recolor` list
in a faction JSON is **per-faction** — every faction can have its own
list of asset names to skip.

Sometimes you want finer control than either:

- "This **one** ribbon should never be recolored, no matter what."
- "This **one** ribbon should have its border forced but stripes left
  alone, even if the user enables Recolor Stripe."

That's what a sidecar is for. Drop a JSON file with the same name as
the ribbon plus `.meta.json`:

```
assets/SORO/ribbons/Heritage.png
assets/SORO/ribbons/Heritage.png.meta.json
```

### Sidecar schema

```json
{
  "no_recolor": false,
  "recolor": {
    "border": true,
    "stripe": false,
    "base":   false
  }
}
```

| Key | Type | What it does |
|---|---|---|
| `no_recolor` | bool | If `true`, the ribbon is rendered unchanged regardless of Settings or `recolor`. Overrides everything else in the sidecar. |
| `recolor.border` | bool | Force the border toggle for this one ribbon. Missing → use the global Settings value. |
| `recolor.stripe` | bool | Same, for stripe. |
| `recolor.base` | bool | Same, for base. |

You only need to write the keys you want to force. Missing keys fall
back to the global Settings toggles, so a sidecar of
`{ "recolor": { "border": true } }` only locks the border for this
one ribbon; stripe and base still follow whatever the user set in
Settings.

### Sidecar vs faction `no_recolor` list

| Use the faction JSON `no_recolor` list | Use a `.meta.json` sidecar |
|---|---|
| You want a **batch** of ribbons in one faction to opt out. | You want one specific ribbon to opt out, or to selectively override individual regions. |
| You're OK editing the faction config. | You want the opt-out to travel with the PNG when it's moved between factions (sidecars are moved by Tools → Move/Rename Ribbon). |

### Edge cases

- **No sidecar / unreadable sidecar / invalid JSON.** Treated as
  defaults (no override). No error message — the ribbon just recolors
  normally.
- **Both `no_recolor: true` and a `recolor` block in the same
  sidecar.** `no_recolor` wins; the `recolor` block is ignored.
- **Sidecar on a ribbon that's also in the faction's
  `no_recolor` list.** Both gates have the same effect — the ribbon is
  not recolored. The faction list is checked first.
- **Sidecar on a non-ribbon (awards/, commendations/).** Ignored.
  Recolor only runs on ribbons, so the sidecar has nothing to
  override.
- **`recolor` block with all three regions `false`.** Equivalent to
  `no_recolor: true` — the ribbon renders untouched (the recolor
  pipeline detects passthrough and skips the work).

## Distributing to a faction

To create a "MDC-only" copy of the engine:

1. Copy the entire project folder.
2. Trim `factions/`: keep `_global.json`, `AOER.json`, `MDC.json`. Delete the rest.
3. Trim `assets/`: keep `AOER/` and `MDC/`. Delete the rest.
4. Ship.

The MDC user's dropdown will only have MDC. They cannot render any
other faction's ribbons because the files don't exist on their disk.

## A note on naming

- The Python module is `factions.py`.
- The config directory is `factions/` (with the trailing slash).
- The asset directory is `assets/`.

Python's import system picks `factions.py` first when you do
`import factions`, so the two named directories never collide.

## Troubleshooting

**A faction doesn't appear in the dropdown.** Check that
`factions/<KEY>.json` exists, is valid JSON, and that the `key`
inside matches the filename.

**A faction appears but has no assets.** Create
`assets/<KEY>/{ribbons,awards,commendations}/` and drop PNGs in.

**A ribbon doesn't get recolored.** It has to be under
`assets/<FACTION>/ribbons/`. Files under `awards/` and
`commendations/` are never recolored regardless of what they look
like.

**On launch I see "Asset warnings" in the status bar.** Look at the
terminal window where you launched the engine — full details print
there. Common causes:

- A PNG referenced by name in a JSON that isn't on disk.
- A filename with a Windows-illegal character like `<>:"/\|?*`.
  Rename the file and reload.
- **Duplicate content** — two PNGs in different faction trees with
  byte-identical contents. Usually means someone copied a ribbon
  between factions instead of moving it. The warning shows the
  SHA-256 prefix and the colliding paths. To fix: pick one as the
  "real" copy and either delete the others or replace them with the
  intended variant.

You can also run the validator anytime from the command line with
`python cli.py --validate` — see [CLI.md](CLI.md).

**My ribbon won't recolor even though it's in `ribbons/`.** Check:

1. Is the faction's `no_recolor` list (in its JSON) blocking it?
2. Is there a `<file>.png.meta.json` sidecar next to it with
   `"no_recolor": true`? See the sidecar section above.
3. Is the active faction the default (passthrough) faction? Default
   faction always renders ribbons in their original art.

**I edited a ribbon PNG but the engine still shows the old one.**
The recolor cache is keyed on file path, not modification time. Use
**File → Reload Assets** (or restart) to flush.

**My loadout dialog shows thumbnails in the wrong faction's
colors.** Switching factions clears the thumbnail cache; if the
dialog was already open during the switch, close and reopen it to
trigger the repaint.

**The "Profile mismatch" prompt appears when I load a PNG.** The
PNG was generated under a different Engine Profile than the one
you're using. Pick **Yes** to switch profiles (safest), **No** to
load anyway and accept that any manual-grid slots outside the new
profile's layout will be dropped, or **Cancel** to abort.
