"""CLI wrapper for the faction-aware ribbon engine.

Examples:
    python cli.py --faction NES --ribbon "Doop" --out doop_nes.png
    python cli.py --faction NES --all-ribbons --out-dir batch/
    python cli.py --list-factions
    python cli.py --diff a.png b.png c.png
    python cli.py --export-loadout my_loadout --faction NES --nametape "John_IA"
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

from PIL import Image, PngImagePlugin

import ribbonengine as engine
from factions import recolor_ribbon


def cmd_list_factions(_args) -> int:
    registry = engine.getFactionRegistry()
    print(f"Default: {registry.default_key}")
    for key, faction in registry.factions.items():
        p = faction.palette
        print(
            f"  {key:<6} groups={list(faction.ribbon_groups)} "
            f"base=#{p.base_color[0]:02x}{p.base_color[1]:02x}{p.base_color[2]:02x} "
            f"stripe=#{p.stripe_color[0]:02x}{p.stripe_color[1]:02x}{p.stripe_color[2]:02x} "
            f"border=#{p.border_color[0]:02x}{p.border_color[1]:02x}{p.border_color[2]:02x}"
        )
    return 0


def _faction_ribbons(faction_key: str) -> list[engine.AssetItem]:
    """Every ribbon visible to a faction: its own dir + every hidden-faction dir."""
    registry = engine.getFactionRegistry()
    seen: set[str] = set()
    out: list[engine.AssetItem] = []
    for key in engine._contributingFactionKeys(registry, faction_key):
        for item in engine.listPngs(engine._factionAssetDir(key, "ribbons")):
            if item.name in seen:
                continue
            seen.add(item.name)
            out.append(item)
    return out


def _find_ribbon(name: str, faction_key: str) -> engine.AssetItem:
    for item in _faction_ribbons(faction_key):
        if item.name.lower() == name.lower():
            return item
    raise SystemExit(f"Ribbon not found for faction {faction_key!r}: {name!r}")


def cmd_render(args) -> int:
    registry = engine.getFactionRegistry()
    if args.faction not in registry.factions:
        raise SystemExit(f"Unknown faction: {args.faction!r}. Known: {registry.names()}")
    engine.setActiveFaction(args.faction)

    if args.all_ribbons:
        out_dir = args.out_dir or os.path.join(engine.baseDir, f"batch_{args.faction}")
        os.makedirs(out_dir, exist_ok=True)
        count = 0
        for item in _faction_ribbons(args.faction):
            rendered = engine.loadRibbonImage(item, factionKey=args.faction)
            rendered.save(os.path.join(out_dir, f"{item.name}.png"))
            count += 1
        print(f"Rendered {count} ribbons -> {out_dir}")
        return 0

    if not args.ribbon:
        raise SystemExit("Provide --ribbon NAME or --all-ribbons")
    item = _find_ribbon(args.ribbon, args.faction)
    rendered = engine.loadRibbonImage(item, factionKey=args.faction)
    out_path = args.out or f"{item.name}_{args.faction}.png"
    rendered.save(out_path)
    print(f"Wrote {out_path}")
    return 0


def _read_metadata(path: str) -> dict | None:
    try:
        with Image.open(path) as img:
            raw = img.info.get("ribbonengine")
    except Exception as exc:
        print(f"  ! {os.path.basename(path)}: can't open ({exc})")
        return None
    if not isinstance(raw, str):
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def cmd_diff(paths: list[str]) -> int:
    """Compare ribbon metadata across 2+ PNGs and print a matrix."""
    if len(paths) < 2:
        raise SystemExit("--diff needs at least 2 paths")
    metas: list[tuple[str, dict]] = []
    for p in paths:
        m = _read_metadata(p)
        if m is None:
            print(f"  ! {os.path.basename(p)}: no ribbonengine metadata, skipping")
            continue
        metas.append((p, m))
    if len(metas) < 2:
        raise SystemExit("Need at least 2 PNGs with metadata to diff")

    labels = [os.path.splitext(os.path.basename(p))[0] for p, _ in metas]
    every = sorted({r for _, m in metas for r in (m.get("ribbons", []) or [])})

    col_w = max(8, min(14, max((len(l) for l in labels), default=8) + 2))
    print("Files:")
    for label, (_, m) in zip(labels, metas):
        print(
            f"  {label}  faction={m.get('faction', '')}  "
            f"nametape={m.get('nameplate', '')!r}"
        )
    print()
    header = f"{'Ribbon':<28}" + "".join(f"{l[:col_w-1]:<{col_w}}" for l in labels)
    print(header)
    print("-" * len(header))
    for ribbon in every:
        row = f"{ribbon[:27]:<28}"
        for _, m in metas:
            has = ribbon in (m.get("ribbons", []) or [])
            row += f"{'X' if has else '.':<{col_w}}"
        print(row)
    return 0


def cmd_export_loadout(args) -> int:
    """Render a single composite tagged as a shareable loadout PNG."""
    registry = engine.getFactionRegistry()
    if args.faction not in registry.factions:
        raise SystemExit(f"Unknown faction: {args.faction!r}. Known: {registry.names()}")
    engine.setActiveFaction(args.faction)
    try:
        engine.loadRibbonGroups()  # warms group cache so renderer sees current assets
    except Exception:
        pass

    # Load the active engine profile so the renderer gets real (non-zero) part
    # coordinates instead of the bootstrap defaults — otherwise the composite
    # would render everything in the top-left corner (the "northwest" bug).
    engine.applyProfile(engine.ensureProfileFile())

    groups = engine.loadRibbonGroups()
    renderer = engine.makeRenderer(groups)
    ribbons = set(args.ribbons or [])
    nametape = args.nametape or ""
    image, _, _ = renderer.buildImage(
        selectedNames=ribbons,
        nameplateText=nametape,
        baseImage=None,
        requireNameForNew=False,
        errorCallback=lambda m: print(f"  ! {m}"),
        faction=args.faction,
        customOffsets={},
        placements=None,
        manualRibbonSlots=None,
        awardSlots=None,
        bonusSlots=None,
        departmentBadge=None,
    )
    if image is None:
        raise SystemExit("Render failed")

    os.makedirs(engine.ribbonOutputDir, exist_ok=True)
    safe = "".join(ch for ch in args.export_loadout if ch.isalnum() or ch in ("-", "_", " ")).strip() or "loadout"
    stamp = datetime.datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    out_path = args.out or os.path.join(
        engine.ribbonOutputDir, f"loadout_{safe.replace(' ', '_')}_{stamp}.png"
    )

    metadata = {
        "version": engine.METADATA_SCHEMA_VERSION,
        "kind": "loadout",
        "loadout_name": args.export_loadout,
        "ribbons": sorted(ribbons),
        "nameplate": nametape,
        "faction": args.faction,
        "custom_offsets": {},
    }
    info = PngImagePlugin.PngInfo()
    info.add_text("ribbonengine", json.dumps(metadata, separators=(",", ":")))
    engine._atomicSaveImage(image, out_path, pnginfo=info)
    print(f"Exported loadout -> {out_path}")
    return 0


def cmd_validate(_args) -> int:
    """Walk assets/ and print warnings; exit non-zero if anything's wrong.

    Suitable for CI: prints one warning per line to stdout, returns 1 if any
    were emitted so a CI job can gate on the exit code.
    """
    registry = engine.getFactionRegistry()
    available: dict = {}
    if os.path.isdir(engine.assetsRoot):
        for factionKey in os.listdir(engine.assetsRoot):
            facDir = os.path.join(engine.assetsRoot, factionKey)
            if not os.path.isdir(facDir):
                continue
            for sub in engine.ASSET_SUBDIRS:
                subDir = os.path.join(facDir, sub)
                if not os.path.isdir(subDir):
                    continue
                stems = {
                    os.path.splitext(name)[0]
                    for name in os.listdir(subDir)
                    if name.lower().endswith(".png")
                }
                available.setdefault(sub, set()).update(stems)
    warnings = registry.validate_assets(available)
    illegal, duplicates = engine.scanAssetTree()
    for entry in illegal:
        warnings.append(f"Windows-illegal filename: {entry}")
    for digest, paths in duplicates.items():
        warnings.append(
            f"Duplicate content (sha256 {digest[:10]}): {', '.join(sorted(paths))}"
        )
    if not warnings:
        print("OK — no asset warnings.")
        return 0
    for w in warnings:
        print(w)
    print(f"\n{len(warnings)} warning(s).")
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Faction-aware ribbon renderer")
    parser.add_argument("--list-factions", action="store_true", help="List factions and exit")
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Scan assets/ for missing files, illegal filenames, and duplicate content; exit non-zero on warnings",
    )
    parser.add_argument("--faction", default=None, help="Faction key (e.g. ANRO, NES)")
    parser.add_argument("--ribbon", default=None, help="Ribbon name (filename without .png)")
    parser.add_argument("--all-ribbons", action="store_true", help="Render every ribbon for the faction")
    parser.add_argument("--out", default=None, help="Output path for single ribbon render")
    parser.add_argument("--out-dir", default=None, help="Output directory for batch render")
    parser.add_argument(
        "--diff",
        nargs="+",
        default=None,
        metavar="PNG",
        help="Compare 2+ engine-exported PNGs and print a ribbon matrix",
    )
    parser.add_argument(
        "--export-loadout",
        default=None,
        metavar="NAME",
        help="Export a shareable loadout PNG (combine with --faction, --ribbons, --nametape)",
    )
    parser.add_argument(
        "--ribbons",
        nargs="+",
        default=None,
        metavar="NAME",
        help="Ribbons to include in --export-loadout (filenames without .png)",
    )
    parser.add_argument(
        "--nametape",
        default=None,
        help="Nametape text for --export-loadout",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.list_factions:
        return cmd_list_factions(args)
    if args.validate:
        return cmd_validate(args)
    if args.diff:
        return cmd_diff(args.diff)
    if args.export_loadout:
        if not args.faction:
            args.faction = engine.getFactionRegistry().default_key
        return cmd_export_loadout(args)
    if not args.faction:
        args.faction = engine.getFactionRegistry().default_key
    return cmd_render(args)


if __name__ == "__main__":
    sys.exit(main())
