# Keyboard Shortcuts

Everything the engine listens to from the keyboard, grouped by what
it's doing.

## File / output

| Shortcut | Action |
|---|---|
| `Ctrl+S` | Generate and save image to `ribbonoutput/` |
| `Ctrl+E` | Export the current setup as a shareable loadout PNG |
| `Ctrl+Shift+C` | Copy the rendered image to the system clipboard |
| `Ctrl+D` | Open the Diff dialog |
| `Ctrl+Esc` | Clear everything (nametape, ribbons, awards, placement grid) |

## History

| Shortcut | Action |
|---|---|
| `Ctrl+Z` | Undo |
| `Ctrl+Y` | Redo |
| `Ctrl+Shift+Z` | Redo (alternate) |

## Search / navigation

| Shortcut | Action |
|---|---|
| `Ctrl+F` | Focus the sidebar search box |
| `/` | Focus the sidebar search box (vim-style) |

## Ribbon Placement panel

These only fire when the **manual placement grid** has keyboard focus
(click it once first).

| Shortcut | Action |
|---|---|
| `←` / `→` / `↑` / `↓` | Move slot selection |
| `Return` | Place the dropdown ribbon in the selected empty slot |
| `Delete` / `Backspace` | Remove the ribbon in the selected slot |
| `R` | Jump to the recolor box (focuses the Border hex field) |

You can also **drag** a ribbon row from the sidebar onto any slot
on the grid. **Right-click** a filled slot to remove it instantly.

## Recolor box

Per-region hex fields support the usual:

| Shortcut | Action |
|---|---|
| `Right-click` on a hex field | Copy/paste menu |
| `Click` the swatch | Open the system color picker |

## Help viewer

Inside a Help → `.md` window:

| Shortcut | Action |
|---|---|
| `Ctrl+C` | Copy selected text |
| Scroll wheel | Scroll the document |

## Notes

- Shortcuts are bound at the application level (`bind_all`), so they
  fire regardless of which child widget has focus — except the
  placement-grid shortcuts above, which intentionally require the
  grid to have focus so that arrow keys inside text fields still
  move the cursor normally.
- All shortcuts are case-insensitive for the letter component (e.g.
  `Ctrl+s` and `Ctrl+S` both save).
