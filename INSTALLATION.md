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
| **Windows** | Double-click `run.bat` |
| **Mac** | Open Terminal in this folder, type `./run.sh` |
| **Linux** | Open a terminal in this folder, type `./run.sh` |

The first launch takes ~30 seconds (it's installing what it needs). After
that, it opens instantly. **That's it — you can stop reading here.**

If the short version didn't work, read on.

---

## What you'll need

Just one thing: **Python 3.9 or newer.** Everything else (Pillow, NumPy)
is installed automatically by the launcher script. You do not need to know
Python to use the engine — you only need it installed.

To check whether you already have Python:

- **Windows:** press the Windows key, type `cmd`, hit Enter. In the black
  window type `python --version` and press Enter. If you see something
  like `Python 3.11.4`, you're set.
- **Mac:** press Cmd+Space, type `Terminal`, hit Enter. Type
  `python3 --version` and press Enter.
- **Linux:** open your terminal. Type `python3 --version`.

If the version is **3.9 or higher**, skip to "Running the engine" below.
Otherwise, install Python first — instructions are in each per-OS section.

---

## Windows

### Step 1 — Install Python (skip if you already have it)

1. Go to <https://www.python.org/downloads/>.
2. Click the big yellow **Download Python 3.x.x** button.
3. Run the installer.
4. **VERY IMPORTANT:** on the first screen of the installer, check the box
   that says **"Add python.exe to PATH"** at the bottom. If you skip this,
   the launcher won't find Python and you'll have to reinstall.
5. Click **Install Now** and wait until it says "Setup was successful."

### Step 2 — Run the engine

1. Find the `Ribbon Engine v3` folder in File Explorer.
2. Double-click **`run.bat`**.
3. A black "Command Prompt" window will appear. The first time you run it,
   it'll say "Creating virtual environment..." — wait about 30 seconds.
4. The Ribbon Engine window opens. You're done.

If Windows shows a blue "Windows protected your PC" warning when you
double-click, click **More info** → **Run anyway**. (This happens because
the `.bat` file isn't signed by Microsoft — it's just a script we wrote.
Look at `run.bat` in Notepad if you want to verify what it does.)

**Do not delete the `.venv` folder** that gets created. That's where the
engine's helper libraries live. If you do delete it, the next launch will
just rebuild it automatically (and take another 30 seconds).

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

### The black window opens, prints stuff, then closes immediately

**Cause:** the launcher hit an error and exited.
**Fix:** open the black window yourself first, then run the launcher from
inside it so you can read the error.

- **Windows:** press Windows key → type `cmd` → press Enter. In the black
  window, type `cd "C:\path\to\Ribbon Engine v3"` (use the real path) and
  press Enter. Then type `run.bat` and press Enter. Whatever error appears
  is the one to fix.
- **Mac/Linux:** the terminal stays open after `./run.sh` exits, so the
  error is already visible.

### "Permission denied" when running `./run.sh`

**Fix:** mark the script as executable, then try again:

```
chmod +x run.sh
```

### Windows SmartScreen blocks `run.bat`

**Cause:** Windows is suspicious of unsigned scripts.
**Fix:** click **More info** → **Run anyway**. You can inspect `run.bat`
in Notepad first if you want to see what it does. It's about 30 lines and
just sets up Python.

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

You can copy the entire `Ribbon Engine v3/` folder to a USB stick or
cloud drive. The launcher rebuilds `.venv/` automatically on each new
machine, so just double-click `run.bat` (or `./run.sh`) on the new
computer. Your settings and loadouts come along with the folder.

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
