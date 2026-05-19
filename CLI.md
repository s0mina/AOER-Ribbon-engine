# Command-Line Renderer

Most people will use the regular GUI (the window that opens when you
double-click `run.bat` or run `./run.sh`). This file is for the **other**
way to use the engine: a command-line tool that renders ribbons without
opening any window.

> **New here?** Read [INSTALLATION.md](INSTALLATION.md) first to get the
> engine set up. Come back here only if you want to batch-export ribbons
> from a terminal.

## What is this for?

The CLI is useful if you want to:

- Render **every ribbon** for a faction into a folder, in one go.
- Quickly check what a single ribbon looks like in a faction's colors
  without clicking through the GUI.
- Run renders from a script (e.g. a build pipeline that regenerates
  ribbon previews when you add a new faction).

If none of those sound like what you want, you can stop reading. The
GUI does everything the CLI does, and more.

## Getting it running

You need the engine installed first — same Python and `.venv` as the
GUI. If `./run.sh` (Mac/Linux) or `run.bat` (Windows) has worked at
least once, you're set.

Then from a terminal inside the project folder:

```
python cli.py --help
```

You should see a short usage summary print out. If you get an error
like "command not found" or "No module named PIL," go through
[INSTALLATION.md](INSTALLATION.md) first.

## Things you can do

### 1. List every faction

```
python cli.py --list-factions
```

Prints each faction's key, its three palette colors, and what ribbon
groups it has. Useful for double-checking your config without
launching the GUI.

### 2. Render one ribbon

```
python cli.py --faction MDC --ribbon "Pressure"
```

Writes `Pressure_MDC.png` next to `cli.py`. The ribbon name is the
PNG filename **without** `.png` and is case-insensitive. You can pick
where it goes with `--out`:

```
python cli.py --faction MDC --ribbon "Pressure" --out pressure_mdc.png
```

### 3. Render every ribbon for a faction

```
python cli.py --faction SORO --all-ribbons
```

Writes all of them into `batch_SORO/` next to `cli.py`. Override the
output folder with `--out-dir`:

```
python cli.py --faction SORO --all-ribbons --out-dir out/soro/
```

If you skip `--faction`, the CLI uses the `default_faction` from
`factions/_global.json`.

### 4. Compare exported PNGs (`--diff`)

```
python cli.py --diff a.png b.png c.png
```

Pass **2 or more** PNGs that were exported by the engine (regular
renders or shared loadouts both work). Prints a text matrix showing
which ribbons are present in which files. Each column is one file;
`X` = present, `.` = absent.

Files that aren't engine-exported (e.g. random PNGs without the
`ribbonengine` metadata tag) are skipped with a `!` warning line —
the diff just runs against whichever files do have metadata.

This is the headless version of the GUI's **Diff…** button. Useful for
scripted comparison ("did this faction's roster change between two
exports?"). The CLI version only does the matrix view — for the
side-by-side image view, use the GUI.

### 5. Export a shareable loadout PNG (`--export-loadout`)

```
python cli.py --export-loadout my_set --faction NES --nametape "John_IA" \
    --ribbons "Pressure" "ANRO Defense Force"
```

Renders a composite and writes it to `ribbonoutput/` with the
loadout data baked into its metadata. The recipient drops the PNG
straight into their `loadouts/` folder and it appears in the
**Loadouts…** dialog — no import prompt, no separate JSON.

`--ribbons` takes a space-separated list of ribbon stems (no `.png`).
Use quotes around names with spaces.

### 6. Validate the asset tree (`--validate`)

```
python cli.py --validate
```

Walks every PNG under `assets/`, checks them against the faction JSON
config, and prints one warning per line for any issue it finds. Three
kinds of warnings:

- **Missing files.** A faction's JSON references a ribbon, but the
  PNG isn't on disk.
- **Windows-illegal filenames.** A PNG's name contains
  `<>:"/\\|?*` — those characters break extraction and PNG save on
  Windows. The engine flags them regardless of which OS you run the
  validator on (most recipients are on Windows).
- **Duplicate content.** Two or more PNGs in different faction trees
  have **byte-identical** contents. Usually means someone copied a
  ribbon between factions; over time the copies drift (one gets
  updated, the other doesn't). The warning shows the SHA-256 prefix
  and the colliding paths so you can decide which to keep.

**Exit code is non-zero if there are any warnings**, so this is safe
to wire into CI. Sample output:

```
$ python cli.py --validate
Duplicate content (sha256 1b8fd3af89): DMP/ribbons/Pressure.png, SORO/ribbons/Pressure.png
Windows-illegal filename: AOER/ribbons/Foo:Bar.png (illegal char(s): :)

2 warning(s).
$ echo $?
1
```

If everything's clean it prints `OK — no asset warnings.` and exits
with `0`.

## Flag reference

| Flag | What it does |
|---|---|
| `--list-factions` | Print all factions and palettes, then quit. |
| `--validate` | Scan assets for missing files, illegal filenames, duplicates. Exits non-zero on warnings. |
| `--faction KEY` | Which faction's palette to use (e.g. `MDC`). |
| `--ribbon NAME` | Render one ribbon by filename (no `.png`). |
| `--all-ribbons` | Render every ribbon the faction is allowed to use. |
| `--out PATH` | Where the single render goes. |
| `--out-dir DIR` | Where the batch renders go. |
| `--diff PNG…` | Compare 2+ engine-exported PNGs and print a matrix. |
| `--export-loadout NAME` | Export a shareable loadout PNG (combine with `--faction`, `--ribbons`, `--nametape`). |
| `--ribbons NAME…` | Ribbon stems to include in `--export-loadout`. |
| `--nametape TEXT` | Nametape for `--export-loadout`. |

`--ribbon` and `--all-ribbons` can't be used together. `--validate`,
`--list-factions`, `--diff`, and `--export-loadout` short-circuit the
normal render flow.

## A few things to know

- **The recolor toggles (`border`, `stripe`, `base`) come from `settings.json`** — the
  same file the GUI writes. There's no CLI flag for them yet. If you
  want a different combo, change it in the GUI once and it sticks.
- **The CLI ignores the sidebar allowlist.** If you ask for a ribbon by
  name and the PNG exists on disk, you'll get it — even if that ribbon
  doesn't normally show up for that faction. (The sidebar filter is a
  GUI thing.)
- **Only ribbons get recolored.** Awards and commendations render in
  their original colors; the CLI doesn't touch them.
- **CLI-exported PNGs from `--ribbon` / `--all-ribbons` don't carry
  loadout metadata.** Those modes render single ribbons, not full
  composites. If you want a PNG you can drag back into the engine to
  restore a full setup (nametape, medals, etc.), use the GUI's
  "Generate Image" button or `cli.py --export-loadout`.
- **Atomic saves.** Every PNG and JSON the CLI writes uses a
  `<file>.tmp` → rename strategy, so if your machine dies mid-write the
  old file stays intact. You should never see corrupt output.
- **Schema version.** Exported loadout PNGs include
  `"version": 2` in their embedded metadata. Older v1 PNGs (no
  version field) still import — the engine treats missing fields as
  defaults.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Worked. |
| Non-zero | Bad arguments, unknown faction, or missing ribbon. |

Errors print one line to stderr explaining what went wrong.

## Using it from Python (for scripters)

If you'd rather call the engine from your own Python code:

```python
import ribbonengine as engine

engine.setActiveFaction("MDC")
ribbons_dir = engine._factionAssetDir("MDC", "ribbons")
for item in engine.listPngs(ribbons_dir):
    img = engine.loadRibbonImage(item, factionKey="MDC")
    img.save(f"out/{item.name}.png")
```

Useful entry points:

- `engine.getFactionRegistry()` → the loaded registry.
  Has `.factions`, `.names()`, `.default_key`, `.get(key)`.
- `engine._factionAssetDir(key, "ribbons" | "awards" | "commendations")`
  → path to a faction's asset folder.
- `engine.listPngs(dir)` → list of `AssetItem` (returns `[]` if the
  folder doesn't exist).
- `engine.loadRibbonImage(item, factionKey="MDC")` → recolored PIL
  image, using the cache.

Adding `assets/MDC/ribbons/NewThing.png` makes it visible to MDC the
next time you reload — no JSON edits needed. See
[FACTIONS.md](FACTIONS.md) for the full asset/faction layout.
