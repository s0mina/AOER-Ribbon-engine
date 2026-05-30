# Installation Guide

This file tells you exactly how to get **Ribbon Engine v3** running on your
computer. Pick the section for your operating system and follow the steps in
order.

If something goes wrong, scroll to the **Troubleshooting** section at the
bottom — almost every common problem is listed there.

---

## TL;DR (the super short version)

| Your computer | Do this |
|---|---|
| **Windows** | [Download the latest release](https://github.com/s0mina/AOER-Ribbon-engine/releases/latest), unzip it, double-click **`AOER-Ribbon-engine.exe`** |
| **Mac** | Open a Terminal in this folder, type `./run.sh` |
| **Linux** | Open a Terminal in this folder, type `./run.sh` |

**Windows needs nothing installed** — the `.exe` is self-contained, no
Python required. On Mac/Linux the first launch takes ~30 seconds (it's
installing what it needs), then opens instantly. **That's it — you can
stop reading here.**

> **Updating (Windows):** you only download manually **once**. From then on
> the `.exe` checks GitHub on launch and, when a newer release exists, offers
> to download and install it for you — your factions, loadouts, and settings
> are left untouched. You can also trigger it from **Help → Check for Updates…**.

If the short version didn't work, read on.

---

## What you'll need

- **Windows:** **nothing.** The `.exe` release is fully self-contained —
  download it, unzip, run. Skip straight to the Windows section below.
- **Mac / Linux:** **Python 3.9 or newer.** Everything else (Pillow,
  NumPy) is installed automatically by the launcher script. You do not
  need to know Python to use the engine — you only need it installed.

To check whether you already have Python (Mac/Linux only):

- **Mac:** press Cmd+Space, type `Terminal`, hit Enter. Type
  `python3 --version` and press Enter.
- **Linux:** open your terminal. Type `python3 --version`.

If the version is **3.9 or higher**, skip to "Running the engine" below.
Otherwise, install Python first — instructions are in each per-OS section.

---

## Windows

**You do not need Python.** The Windows download is a ready-to-run
application. Nothing gets installed on your computer and nothing is
permanently changed — it all lives inside the folder you unzip.

### Step 1 — Download

1. Go to the releases page:
   <https://github.com/s0mina/AOER-Ribbon-engine/releases/latest>
2. Under **Assets**, click **`AOER-Ribbon-engine-windows.zip`** to download it.

### Step 2 — Unzip

1. Find the downloaded `AOER-Ribbon-engine-windows.zip` (usually in your
   Downloads folder).
2. Right-click it → **Extract All…** → **Extract**. You now have a folder
   with the application inside.

> **Don't run it from inside the zip.** Windows lets you peek into a zip
> without unzipping, but the app won't find its files that way. Extract
> first, then open the extracted folder.

### Step 3 — Run

Open the extracted folder and double-click **`AOER-Ribbon-engine.exe`**.
The Ribbon Engine window opens. That's it.

If Windows shows a blue **"Windows protected your PC"** warning, click
**More info** → **Run anyway**. This happens with any app that isn't
code-signed (signing costs money per year); it does not mean anything is
wrong with the app.

### Step 4 — Add your faction artwork

If you were sent ribbon artwork as a separate zip, extract its contents
into the **`assets/`** folder next to the `.exe`, keeping the faction
folder names exactly as given (e.g. `assets/MDC/ribbons/...`). The engine
picks them up the next time you open it — no rebuild needed.

**To move the app**, just move the whole extracted folder (the `.exe`
plus the `assets/`, `factions/`, etc. folders travel together). To
uninstall, delete the folder. Nothing is left behind anywhere else.

---

## Mac

### Step 1 — Install Python (skip if you already have it)

macOS comes with an old version of Python that won't work. Get a fresh one:

1. Go to <https://www.python.org/downloads/>.
2. Click **Download Python 3.x.x**.
3. Open the `.pkg` installer that downloads.
4. Click through the installer with the defaults — no special options
   needed on Mac.

### Step 2 — Open Terminal in the project folder

1. Find the `Ribbon Engine v3` folder in Finder.
2. Right-click the folder → **Services** → **New Terminal at Folder**.
   (If you don't see this option, open Terminal manually and type `cd `,
   then drag the folder onto the Terminal window and press Enter.)

### Step 3 — Run the engine

In the Terminal window, type this and press Enter:

```
./run.sh
```

The first run takes ~30 seconds. After that the GUI opens instantly.

If Terminal says **"permission denied"**, type this once and try again:

```
chmod +x run.sh
```

If macOS blocks the script with a security popup, go to **System Settings →
Privacy & Security**, scroll down, and click **Allow Anyway** for `run.sh`.

---

## Linux

### Step 1 — Install Python and Tkinter

Tkinter (the window/button library) is not always bundled with Python on
Linux. Install both:

| Distro | Command |
|---|---|
| Ubuntu / Debian / Mint | `sudo apt install python3 python3-venv python3-tk` |
| Fedora | `sudo dnf install python3 python3-tkinter` |
| Arch / Manjaro | `sudo pacman -S python tk` |

### Step 2 — (Optional) Install clipboard helper

If you want the "Paste Image from Clipboard" feature to work:

- **Wayland:** `sudo apt install wl-clipboard` (or your distro's equivalent)
- **X11:** `sudo apt install xclip`

This is optional — everything else works without it.

### Step 3 — Run the engine

Open a terminal in the project folder and type:

```
./run.sh
```

If you get **"permission denied"**, run this once:

```
chmod +x run.sh
```

---

## Running the engine (manual mode)

If the launcher script doesn't work, you can do the steps by hand:

```
python -m venv .venv

# Windows:
.venv\Scripts\activate

# Mac/Linux:
source .venv/bin/activate

pip install -r requirements.txt
python ribbonengine.py
```

---

## Troubleshooting

### "Python is not recognized as an internal or external command"

**Cause:** the Python installer didn't add Python to your PATH.
**Fix:** uninstall Python from Control Panel → Programs, then reinstall
from <https://www.python.org/downloads/> and **check the "Add python.exe
to PATH" box** on the first screen.

### "python3: command not found" (Mac/Linux)

**Cause:** Python isn't installed.
**Fix:** see the Mac or Linux section above.

### "No module named tkinter"

**Cause:** Tkinter is missing.
**Fix on Linux:** `sudo apt install python3-tk` (or your distro's name for
the same package — see the Linux table above).
**Fix on Windows/Mac:** reinstall Python from python.org with default
options — Tkinter ships in the installer.

### The app won't open / closes immediately (Mac/Linux source run)

**Cause:** the launcher hit an error and exited. (Windows users on the
`.exe` don't hit this — if the `.exe` won't open, see the SmartScreen
note below.)
**Fix (Mac/Linux):** the terminal stays open after `./run.sh` exits, so
the error is already visible — read it there.

### "Permission denied" when running `./run.sh`

**Fix:** mark the script as executable, then try again:

```
chmod +x run.sh
```

### Windows SmartScreen blocks `AOER-Ribbon-engine.exe`

**Cause:** Windows warns about any app that isn't code-signed, which is
normal for free/indie software (signing costs money per year).
**Fix:** click **More info** → **Run anyway**. The `.exe` is built
automatically from this public source code by GitHub Actions, so you can
see exactly what goes into it.

### The window opens but my faction isn't in the dropdown

**Cause:** the JSON file for that faction is missing under `factions/`,
**or** the asset directory `assets/<FACTION>/` doesn't exist.
**Fix:** both must be present. The folder name and the `"key"` field
inside the JSON have to match exactly (case-sensitive on Linux, but be
consistent everywhere for safety).

### The window opens but a ribbon I expect isn't there

**Cause:** the PNG isn't physically in your faction's asset folder.
**Fix:** drop the PNG into `assets/<YOUR_FACTION>/ribbons/`. It'll appear
next time you re-open the engine (or switch factions and switch back).

The engine reads the **folder contents** as the allowlist on purpose:
if the file isn't there for your faction, the engine cannot draw it.

### "Asset warnings: N (see terminal for details)" in the status bar

**Cause:** the validator found a problem with one or more asset files.
**Fix:** look in the terminal/command-prompt window where you launched
the engine — full details are printed there. Usually it's either a
missing PNG referenced in a JSON, or a filename with a Windows-illegal
character like `:` or `?`. Rename the file (no `<>:"/\|?*`) and reload.

### The engine starts but my saved settings aren't loading

**Cause:** `settings.json` is missing or corrupted.
**Fix:** delete `settings.json` from the project folder. The engine will
recreate it with defaults the next time it runs. Your loadouts (in the
`loadouts/` folder) are separate and not affected.

### I generated a PNG but can't find it

**Look in the `ribbonoutput/` folder** inside the project. It's created
automatically the first time you save. The filename is
`[YOUR_NAMETAPE]_[YEAR-MONTH-DAY-HOUR-MINUTE-SECOND].png`,
e.g. `[John_IA]_[2026-05-17-23-17-56].png`.

### "I want to use this on more than one computer"

- **Windows:** copy the entire extracted application folder (the `.exe`
  plus its `assets/`, `factions/`, etc.) to a USB stick or cloud drive.
  Double-click `AOER-Ribbon-engine.exe` on the other machine — nothing to
  install. Your settings and loadouts travel with the folder.
- **Mac/Linux:** copy the project folder; the launcher rebuilds `.venv/`
  automatically on each new machine, so just run `./run.sh`.

### "Can I use this without internet?"

After the **first** run downloads Pillow and NumPy, yes — everything is
offline. The first run needs internet for the package install (about 30
MB total).

### Something else broke

Send a screenshot of the **whole terminal window** (not just the GUI) to
the tech support contact below. The error text printed there is what
they'll need to help.

**Tech support:** Lolcraft101_owner — Discord username `.somina`
(Discord ID `1051611866901786654`).
