"""Display-free ribbon compositor.

``RibbonRenderer.buildImage`` is the pixel core that places ribbons, medals,
commendations, badges, and the nametape onto the 128x128 canvas. It is pure
PIL — no Tkinter — and reads every layout value from a :class:`LayoutProfile`
instead of the module-level globals it used to depend on. That is what makes it
importable (and testable) on a box with no display.

The asset-loading side (turning an ``AssetItem`` into a recolored ``Image``,
loading character glyphs, locating the Characters dir) lives in the GUI module
and is *injected* at construction. The renderer never imports it, so this file
depends only on PIL + ``profiles`` and can be unit-tested with stub loaders.
"""

from __future__ import annotations

import os
from typing import Callable, Optional

from PIL import Image

from profiles import LayoutProfile


def _buildRowImages(
    items,
    safeLoad: Callable[[object], Optional[Image.Image]],
):
    """Load each item in a row, returning ``(rows, totalWidth, rowHeight)``.

    Pure: depends only on the images it loads, not on any layout config.
    """
    rowImages = []
    totalWidth = 0
    rowHeight = 0
    for item in items:
        piece = safeLoad(item)
        if piece is None:
            continue
        w, h = piece.size
        rowImages.append((item, piece, w, h))
        totalWidth += w
        rowHeight = max(rowHeight, h)
    return rowImages, totalWidth, rowHeight


def _centeredRowStart(totalWidth: int, areaX: int, areaWidth: int, itemCount: int, offset: int = 0) -> int:
    rowCenter = areaX + areaWidth // 2
    if itemCount == 1:
        return rowCenter - totalWidth // 2 - 1 + offset
    if itemCount == 4:
        return rowCenter - totalWidth // 2 + 1 + offset
    return rowCenter - totalWidth // 2 + offset


def _rightAlignedRowStart(totalWidth: int, itemCount: int, areaX: int, areaWidth: int, offset: int = 0) -> int:
    widthWithSpacing = totalWidth - max(itemCount - 1, 0)
    rightEdge = areaX + areaWidth - 1
    return rightEdge - widthWithSpacing + offset


class RibbonRenderer:
    """Composites the final ribbon image from a category->items map.

    Parameters
    ----------
    groups:
        ``{category: [AssetItem, ...]}`` — the selectable assets per section.
    layout:
        The :class:`LayoutProfile` driving every coordinate/offset/capacity.
    load_ribbon_image / render_ribbon_with_colors / load_character_image:
        Injected asset loaders (kept in the GUI module so this file stays
        display-free). ``load_ribbon_image(item, factionKey=...)`` and
        ``render_ribbon_with_colors(item, factionKey, colors)`` return RGBA
        images; ``load_character_image(ch)`` returns a glyph or raises
        ``FileNotFoundError``.
    characters_dir:
        Folder holding ``Nameplate.png`` and the glyph PNGs.
    award_medal_names / bonus_medal_names:
        Profile-driven sets classifying pocket medals.
    """

    def __init__(
        self,
        groups,
        layout: LayoutProfile,
        *,
        load_ribbon_image: Callable,
        render_ribbon_with_colors: Callable,
        load_character_image: Callable,
        characters_dir: str,
        award_medal_names,
        bonus_medal_names,
    ):
        self.groups = groups
        self.layout = layout
        self.load_ribbon_image = load_ribbon_image
        self.render_ribbon_with_colors = render_ribbon_with_colors
        self.load_character_image = load_character_image
        self.characters_dir = characters_dir
        self.award_medal_names = set(award_medal_names)
        self.bonus_medal_names = set(bonus_medal_names)

    @staticmethod
    def _newUsedSlots() -> dict[str, set[str]]:
        return {
            "sacks": set(),
            "corpus": set(),
            "gorget": set(),
            "spbadge": set(),
            "commendations": set(),
            "ribbons": set(),
        }

    def _buildPocketCenters(self, baseX: int, selectedCount: int) -> list[int]:
        spacing = self.layout.pocket_col_spacing
        slotMap = {
            "left": baseX,
            "middle": baseX + spacing,
            "right": baseX + (spacing * 2),
        }
        order = (
            self.layout.medal_single_order
            if selectedCount == 1
            else self.layout.medal_multi_order
        )
        return [slotMap[token] for token in order]

    def _computeRibbonSlotGrid(self, maxRows: int = 10, ribbonW: int = 11, ribbonH: int = 5) -> list[tuple[int, int]]:
        """Fixed slot positions for manual ribbon placement.

        Each row is treated as full-capacity so the grid is deterministic
        regardless of how many ribbons are placed. Slot 0 is the leftmost ribbon
        in the bottom row (visually matches the engine's auto-layout, which
        stacks upward).
        """
        lp = self.layout
        originX, originY = lp.part_coords["ribbons"]
        slots: list[tuple[int, int]] = []
        y = originY
        for rowNum in range(1, maxRows + 1):
            if rowNum < lp.right_start_row:
                cap, alignRight = lp.centered_row_capacity, False
            elif rowNum == lp.right_start_row:
                cap, alignRight = lp.right_first_row_capacity, True
            else:
                cap, alignRight = lp.right_subsequent_row_capacity, True
            widthWithSpacing = cap * ribbonW - max(cap - 1, 0)
            if alignRight:
                # Match _rightAlignedRowStart: rightEdge = areaX + areaWidth - 1
                xStart = originX + lp.ribbon_area_width - 1 - widthWithSpacing + lp.ribbons_right_align_offset
            else:
                xStart = originX + (lp.ribbon_area_width - widthWithSpacing) // 2 + lp.ribbons_right_align_offset
            for i in range(cap):
                slots.append((xStart + i * (ribbonW - 1), y))
            y -= (ribbonH - 1)
        return slots

    def buildImage(
        self,
        selectedNames: set[str],
        nameplateText: str,
        baseImage: Optional[Image.Image],
        requireNameForNew: bool,
        errorCallback: Optional[Callable[[str], None]],
        faction: Optional[str] = None,
        customOffsets: Optional[dict[str, tuple[int, int]]] = None,
        placements: Optional[list[dict]] = None,
        manualRibbonSlots: Optional[dict[int, str]] = None,
        manualSlotColors: Optional[dict[int, dict[str, str]]] = None,
        awardSlots: Optional[list[str]] = None,
        bonusSlots: Optional[list[str]] = None,
        departmentBadge: Optional[str] = None,
    ) -> tuple[Optional[Image.Image], Optional[dict[str, set[str]]], Optional[set[str]]]:
        """Composite a 128×128 ribbon image.

        `customOffsets` (name -> (dx, dy)) shifts individual ribbons from their
        algorithmic position — used by the drag-to-place feature. If supplied,
        `placements` is filled with `{name, category, x, y, w, h}` dicts so
        callers can hit-test mouse clicks back to the assets that were drawn.
        """
        lp = self.layout
        offsets = customOffsets or {}

        def _record_paste(target_img, piece, xy, name, category):
            dx, dy = offsets.get(name, (0, 0))
            final = (xy[0] + dx, xy[1] + dy)
            target_img.paste(piece, final, piece)
            if placements is not None:
                placements.append({
                    "name": name,
                    "category": category,
                    "x": final[0],
                    "y": final[1],
                    "w": piece.size[0],
                    "h": piece.size[1],
                })

        try:
            if baseImage is None:
                if requireNameForNew and nameplateText.strip() == "":
                    raise ValueError("Nametape cannot be blank for a new image.")
                baseImg = Image.new("RGBA", (lp.image_size, lp.image_size), (255, 255, 255, 0))
            else:
                baseImg = baseImage.copy().convert("RGBA")

            usedSlots = self._newUsedSlots()

            nameplateImg = None
            nameplateWidth = lp.default_nameplate_width
            nameplatePath = os.path.join(self.characters_dir, "Nameplate.png")
            if os.path.exists(nameplatePath):
                with Image.open(nameplatePath) as img:
                    nameplateImg = img.convert("RGBA")
                    nameplateWidth = nameplateImg.size[0]

            missingAssets: set[str] = set()

            def safeLoad(item) -> Optional[Image.Image]:
                try:
                    return self.load_ribbon_image(item, factionKey=faction)
                except Exception:
                    missingAssets.add(item.name)
                    return None

            def selectedItems(category: str) -> list:
                return [item for item in self.groups[category] if item.name in selectedNames]

            # Award / Bonus medals. There are two mirrored medal rows that
            # share one Y anchor and identical pocket geometry, so a medal
            # looks the same in either row:
            #   * Award row  -> centred under the RIBBONS block (right side).
            #   * Bonus row  -> centred under the NAMETAPE (left side); the
            #     department badge, when enabled, replaces this row.
            # Any medal can sit in any of the 6 slots ("bonus" is really just
            # an overflow set of awards). Explicit slot lists allow duplicates
            # (e.g. triple Diamond Medal); the checkbox-derived fallback dedupes.
            if awardSlots is not None or bonusSlots is not None:
                medalByName = {it.name: it for it in self.groups["sacks"]}
                awardMedals = [medalByName[n] for n in (awardSlots or []) if n and n in medalByName]
                bonusMedals = [medalByName[n] for n in (bonusSlots or []) if n and n in medalByName]
            else:
                selectedMedals = selectedItems("sacks")
                awardMedals = [item for item in selectedMedals if item.name in self.award_medal_names]
                bonusMedals = [item for item in selectedMedals if item.name in self.bonus_medal_names]

            # The department badge takes over the bonus (nametape) row.
            useBadge = bool(departmentBadge) and departmentBadge != "NONE"
            if useBadge:
                bonusMedals = []

            # Cap each row at the per-side limit.
            if len(awardMedals) > lp.max_medals_per_side:
                if errorCallback:
                    errorCallback(f"Only {lp.max_medals_per_side} award medals can be applied; extra selections are ignored.")
                awardMedals = awardMedals[:lp.max_medals_per_side]
            if len(bonusMedals) > lp.max_medals_per_side:
                if errorCallback:
                    errorCallback(f"Only {lp.max_medals_per_side} bonus medals can be applied; extra selections are ignored.")
                bonusMedals = bonusMedals[:lp.max_medals_per_side]

            if awardMedals or bonusMedals or useBadge:
                nameplateCenterX = lp.part_coords["nametape"][0] + (nameplateWidth // 2)
                ribbonsCenterX = lp.part_coords["ribbons"][0] + (lp.ribbon_area_width // 2)
                yTop = lp.part_coords["sacks"][1]

                # Bonus row under the nametape (left); award row under the
                # ribbons (right). pocket_right_offset nudges the right row.
                bonusBaseX = nameplateCenterX + lp.pocket_x_offset
                awardBaseX = ribbonsCenterX + lp.pocket_x_offset + lp.pocket_right_offset

                # When explicit slot lists are provided, allow duplicates (the
                # user picked the same medal in multiple slots intentionally).
                dedupe = awardSlots is None and bonusSlots is None

                def slotOffset(offsets, idx):
                    # Per-slot (x, y) nudge added to the auto position; 0,0 when
                    # the profile defines no offset for this slot index.
                    return offsets[idx] if idx < len(offsets) else (0, 0)

                def rowSpacing(items, override):
                    # Spacing precedence: explicit per-row spacing > legacy
                    # global pocket_col_spacing > auto (widest medal + 1px).
                    # Auto is what stops medals stacking when nothing is set.
                    if override > 0:
                        return override
                    if lp.pocket_col_spacing > 0:
                        return lp.pocket_col_spacing
                    widths = []
                    for it in items:
                        piece = safeLoad(it)
                        if piece is not None:
                            widths.append(piece.size[0])
                    return (max(widths) + 1) if widths else 1

                def rowCenters(baseX, count, spacing):
                    # `count` slots centred on baseX, laid out left-to-right.
                    return [int(baseX + (i - (count - 1) / 2) * spacing) for i in range(count)]

                def pasteRow(items, baseX, offsets, spacingOverride):
                    spacing = rowSpacing(items, spacingOverride)
                    centers = rowCenters(baseX, len(items), spacing)
                    for i, (item, cx) in enumerate(zip(items, centers)):
                        piece = safeLoad(item)
                        if piece is None:
                            continue
                        if dedupe and item.name in usedSlots["sacks"]:
                            continue
                        w, _ = piece.size
                        ox, oy = slotOffset(offsets, i)
                        _record_paste(baseImg, piece, (int(cx - w / 2) + ox, yTop + oy), item.name, "sacks")
                        usedSlots["sacks"].add(item.name)

                pasteRow(awardMedals, awardBaseX, lp.award_slot_offsets, lp.award_row_spacing)
                pasteRow(bonusMedals, bonusBaseX, lp.bonus_slot_offsets, lp.bonus_row_spacing)

                # Department badge occupies the bonus (nametape) row centre.
                if useBadge:
                    badgeItem = next((it for it in self.groups.get("spbadge", []) if it.name == departmentBadge), None)
                    if badgeItem is not None:
                        piece = safeLoad(badgeItem)
                        if piece is not None:
                            w, _ = piece.size
                            _record_paste(baseImg, piece, (int(bonusBaseX - w / 2), yTop), badgeItem.name, "sacks")

            # Gorgets
            for item in self.groups["gorget"]:
                if item.name in selectedNames and item.name not in usedSlots["gorget"]:
                    piece = safeLoad(item)
                    if piece is not None:
                        _record_paste(baseImg, piece, lp.part_coords["gorget"], item.name, "gorget")
                        usedSlots["gorget"].add(item.name)

            # Special badges
            for item in self.groups["spbadge"]:
                if item.name in selectedNames and item.name not in usedSlots["spbadge"]:
                    piece = safeLoad(item)
                    if piece is not None:
                        _record_paste(baseImg, piece, lp.part_coords["spbadge"], item.name, "spbadge")
                        usedSlots["spbadge"].add(item.name)

            # Commendations
            selectedComm = selectedItems("commendations")
            yStart = lp.part_coords["commendations"][1]
            maxPerRow = 4
            rowCount = 0
            secondRow = False

            while selectedComm:
                rowCount += 1
                if rowCount >= 2:
                    secondRow = True

                row = selectedComm[:maxPerRow]
                selectedComm = selectedComm[maxPerRow:]

                rowImages, totalWidth, rowHeight = _buildRowImages(row, safeLoad)
                if not rowImages:
                    continue

                xCursor = _centeredRowStart(
                    totalWidth=totalWidth,
                    areaX=lp.part_coords["commendations"][0],
                    areaWidth=lp.ribbon_area_width,
                    itemCount=len(row),
                )

                for item, piece, w, _ in rowImages:
                    if item.name not in usedSlots["commendations"]:
                        _record_paste(baseImg, piece, (xCursor, yStart), item.name, "commendations")
                        xCursor += w - 1
                        usedSlots["commendations"].add(item.name)
                yStart -= rowHeight - 1

            # Corpus commendations
            selectedCorpus = selectedItems("corpus")
            if selectedCorpus:
                yStart = lp.part_coords["corpus"][1]
                if not secondRow:
                    yStart += 3

                while selectedCorpus:
                    row = selectedCorpus[:maxPerRow]
                    selectedCorpus = selectedCorpus[maxPerRow:]

                    rowImages, totalWidth, rowHeight = _buildRowImages(row, safeLoad)
                    if not rowImages:
                        continue

                    xCursor = _centeredRowStart(
                        totalWidth=totalWidth,
                        areaX=lp.part_coords["corpus"][0],
                        areaWidth=lp.ribbon_area_width,
                        itemCount=len(row),
                        offset=lp.corpus_x_offset,
                    )

                    for item, piece, w, _ in rowImages:
                        if item.name not in usedSlots["corpus"]:
                            _record_paste(baseImg, piece, (xCursor, yStart), item.name, "corpus")
                            xCursor += w - 1
                            usedSlots["corpus"].add(item.name)

                    yStart -= rowHeight

            # Ribbons — manual slot placement bypasses the auto-flow layout.
            if manualRibbonSlots:
                ribbonByName = {item.name: item for item in self.groups["ribbons"]}
                slotPositions = self._computeRibbonSlotGrid()
                for slotIdx, ribbonName in manualRibbonSlots.items():
                    if slotIdx < 0 or slotIdx >= len(slotPositions):
                        continue
                    item = ribbonByName.get(ribbonName)
                    if item is None:
                        continue
                    slotOverride = (manualSlotColors or {}).get(slotIdx)
                    if slotOverride:
                        try:
                            piece = self.render_ribbon_with_colors(item, faction, slotOverride)
                        except Exception:
                            piece = safeLoad(item)
                    else:
                        piece = safeLoad(item)
                    if piece is None:
                        continue
                    # Manual placement allows duplicates — the user explicitly
                    # picked each slot, so we don't dedup by name here.
                    _record_paste(baseImg, piece, slotPositions[slotIdx], ribbonName, "ribbons")
                selectedRibbons = []
            else:
                selectedRibbons = selectedItems("ribbons")
            yStart = lp.part_coords["ribbons"][1]
            rowNumber = 1

            while selectedRibbons:
                if rowNumber < lp.right_start_row:
                    maxInRow = lp.centered_row_capacity
                    alignRight = False
                elif rowNumber == lp.right_start_row:
                    maxInRow = lp.right_first_row_capacity
                    alignRight = True
                else:
                    maxInRow = lp.right_subsequent_row_capacity
                    alignRight = True

                row = selectedRibbons[:maxInRow]
                selectedRibbons = selectedRibbons[maxInRow:]

                rowImages, totalWidth, rowHeight = _buildRowImages(row, safeLoad)
                if not rowImages:
                    rowNumber += 1
                    continue

                if alignRight:
                    xCursor = _rightAlignedRowStart(
                        totalWidth=totalWidth,
                        itemCount=len(rowImages),
                        areaX=lp.part_coords["ribbons"][0],
                        areaWidth=lp.ribbon_area_width,
                        offset=lp.ribbons_right_align_offset,
                    )
                else:
                    widthWithSpacing = totalWidth - max(len(rowImages) - 1, 0)
                    xCursor = lp.part_coords["ribbons"][0] + (
                        (lp.ribbon_area_width - widthWithSpacing) // 2
                    ) + lp.ribbons_right_align_offset

                for item, piece, w, _ in rowImages:
                    if item.name not in usedSlots["ribbons"]:
                        _record_paste(baseImg, piece, (xCursor, yStart), item.name, "ribbons")
                        xCursor += w - 1
                        usedSlots["ribbons"].add(item.name)

                yStart -= rowHeight - 1
                rowNumber += 1

            # Nametape
            if nameplateText.strip():
                npX, npY = lp.part_coords["nametape"]
                if nameplateImg is None:
                    if not os.path.exists(nameplatePath):
                        raise FileNotFoundError(f"Missing nameplate image: {nameplatePath}")
                    with Image.open(nameplatePath) as img:
                        nameplateImg = img.convert("RGBA")

                baseImg.paste(nameplateImg, (npX, npY), nameplateImg)

                letters: list[tuple[Optional[Image.Image], int]] = []
                totalWidth = 0
                for ch in nameplateText.upper():
                    try:
                        letterImg = self.load_character_image(ch)
                    except FileNotFoundError:
                        if ch == " ":
                            letters.append((None, 2))
                            totalWidth += 2
                        continue
                    w, _ = letterImg.size
                    letters.append((letterImg, w))
                    totalWidth += w

                if letters:
                    totalWidth += lp.nameplate_letter_spacing * (len(letters) - 1)
                    startX = npX + (nameplateImg.size[0] - totalWidth) // 2
                    for index, (letterImg, width) in enumerate(letters):
                        if letterImg is not None:
                            baseImg.paste(letterImg, (startX, npY + 1), letterImg)
                        startX += width
                        if index < len(letters) - 1:
                            startX += lp.nameplate_letter_spacing

            if missingAssets and errorCallback:
                missingList = ", ".join(sorted(missingAssets))
                errorCallback(f"Missing assets: {missingList}")

            return baseImg, usedSlots, missingAssets

        except Exception as exc:
            if errorCallback:
                errorCallback(str(exc))
            return None, None, None
