# Advanced Auto Clicker (TUI)

A cross-platform auto clicker that runs in your terminal. Works on Windows,
macOS and Linux, installs without admin rights, and needs no GUI toolkit.

```
┌─ Advanced Auto Clicker ─────────────────────────────────┐
│  Clicking │ Advanced │ Stay online │ Profiles │ Help    │
│                                                          │
│  Click interval                                          │
│   Hours    Minutes   Seconds   Millisec                  │
│  [  0  ]  [  0   ]  [  1   ]  [  0    ]                  │
│  Every 1s  (~1.0 clicks/sec)                             │
│                                                          │
│  Mouse button                                            │
│  (•) Left  ( ) Right  ( ) Middle                         │
│                                                          │
│  Idle   Press F6 or Start to begin.                      │
│  [        Start        ][        Stop        ]           │
└──────────────────────────────────────────────────────────┘
```

## Features

- Click interval down to 1 ms, set in hours / minutes / seconds / milliseconds
- Left, right or middle button
- Run forever or for a fixed number of clicks
- Click at the cursor, or at a fixed screen position you pick by clicking
- **Idle mode** — only click while you are away from the keyboard
- **Auto-stop timer** — stop automatically after a set duration
- **Stay online** — keep Microsoft Teams (and Slack, and the rest) from
  flipping you to *Away*, with its own auto-stop timer
- **Stops when you lock or suspend** the machine, so nothing carries on
  clicking without you
- **Global hotkeys** that work while another window has focus (default `F6` /
  `F7` / `F8`)
- Named profiles, plus automatic restore of your last session

## Install

### Option 1 — standalone binary (no Python, no admin)

Download the file for your platform from the
[latest release](https://github.com/jacksonsieben/auto-clicker-tui/releases/latest)
and run it. Nothing is installed and no admin rights are required.

| Platform | File |
| --- | --- |
| Windows | `autoclicker-windows.exe` |
| macOS | `autoclicker-macos` |
| Linux | `autoclicker-linux` |

On macOS and Linux, mark it executable first:

```bash
chmod +x ./autoclicker-macos
./autoclicker-macos
```

> If your workplace blocks unsigned `.exe` files from `Downloads`, try the
> `autoclicker-windows-folder.zip` build instead: extract it anywhere you can
> write to and run `autoclicker.exe` from inside the extracted folder. It does
> not unpack itself into `%TEMP%`, which is what most application-control
> policies actually block.

### Option 2 — run it with `uvx` (no install at all)

If [`uv`](https://docs.astral.sh/uv/) is available:

```bash
uvx advanced-auto-clicker-tui
```

### Option 3 — pip

**Windows and macOS:**

```bash
pip install --user advanced-auto-clicker-tui
autoclicker
```

**Linux:**

```bash
pip install --user advanced-auto-clicker-tui && pip install --user --no-deps pynput
autoclicker
```

The extra step exists because `pynput` declares a dependency on `evdev`, which
ships source-only and needs a C compiler plus kernel headers. The X11 backend
this app uses never imports it, so `--no-deps` gives you a working install with
no build step. If you do have a toolchain and prefer one command, use
`pip install --user 'advanced-auto-clicker-tui[linux-full]'`.

## Usage

```bash
autoclicker                     # start with your last session restored
autoclicker --profile grind     # start from a saved profile
autoclicker --list-profiles
autoclicker --check             # report whether input and lock detection work here
autoclicker --config-dir
```

### Keys

| Key | Action |
| --- | --- |
| `F6` | Start / stop clicking (global — works from any window) |
| `F7` | Stop everything (global) |
| `F8` | Start / stop staying online (global) |
| `Ctrl+R` | Start / stop clicking (in-app) |
| `Ctrl+T` | Start / stop staying online (in-app) |
| `Ctrl+S` | Save settings |
| `F1` | Help |
| `Ctrl+Q` | Quit |
| `Tab` / `Shift+Tab` | Move between fields |

All three global hotkeys are rebindable — the first two on the **Advanced**
tab, the third on **Stay online**. Press *Record* and hit the combination you
want.

## Stay online

Teams, Slack and the rest do not decide you are away by watching their own
window. They read the operating system's idle timer, the same one that starts
your screensaver. A few minutes with no keyboard or mouse input and your
status flips to *Away*, whether or not you are reading the screen.

The **Stay online** tab resets that timer for you. Every minute (configurable)
it sends one harmless input event:

| Method | What it does |
| --- | --- |
| A keystroke | Taps `F15` — a key no physical keyboard has, so nothing is bound to it. `Scroll Lock` and `Shift` are also available. |
| A mouse nudge | Moves the pointer one pixel and straight back. |
| Both | Sends both. |

Two things keep it out of your way:

- **Only nudge when the machine is already idle** (on by default) — if you are
  actually typing, the timer is already being reset and it does nothing.
- **The auto-stop timer** (8 hours by default) — it will not still be holding
  you *Available* tomorrow morning.

It keeps running while your screen is locked, which is usually the point, but
stops if the machine suspends. Clicking and staying online are mutually
exclusive: starting one stops the other, since the nudges would otherwise read
as you being at the keyboard.

## Locking and suspending

By default, clicking stops when the screen locks or the machine suspends, so
you never come back to a run that carried on without you. Turn it off under
*When the session goes away* on the **Advanced** tab.

A suspend is always detected — it is inferred from the gap in the clock. Lock
detection needs support from the desktop, and the **Advanced** tab tells you
what this machine has:

| Platform | Mechanism |
| --- | --- |
| Windows | `OpenInputDesktop` (the lock screen is a different desktop) |
| macOS | Quartz `CGSSessionScreenIsLocked`, falling back to `ioreg` |
| Linux | logind `LockedHint`, falling back to the `org.freedesktop.ScreenSaver` D-Bus interface |

None of these add a dependency. If a machine has no working mechanism, the tab
says so and only suspends will stop the run.

## Platform notes

These apply to every auto clicker, not just this one.

**macOS** needs Accessibility permission before any app may synthesize clicks.
Grant it to your terminal under *System Settings → Privacy & Security →
Accessibility*. On a managed corporate Mac this can be blocked by MDM policy.

**Linux** needs an X11/Xorg session. Wayland deliberately prevents applications
from injecting input, so the clicker cannot work under it. Check with:

```bash
echo $XDG_SESSION_TYPE     # should print "x11"
```

Most distributions offer an *"on Xorg"* option on the login screen.

**Windows** works as-is. No admin rights and no special permissions.

Run `autoclicker --check` and it will tell you which of these applies to you.

## Configuration

Settings live as JSON in:

| Platform | Path |
| --- | --- |
| Windows | `%APPDATA%\advanced-auto-clicker\` |
| macOS | `~/Library/Application Support/advanced-auto-clicker/` |
| Linux | `~/.config/advanced-auto-clicker/` |

`last_session.json` is written automatically; every other file is a named
profile. Config files from the older Tkinter version of this app are read
automatically, so your existing setup carries over.

## Development

```bash
make venv     # create .venv and install the project (plus the Linux pynput fix)
make run      # run from source
make test     # run the test suite
make check    # report whether input control works on this machine
```

Or without `make`:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[build]'
.venv/bin/pip install --no-deps pynput    # Linux only
.venv/bin/autoclicker
```

Build a standalone binary for the current platform:

```bash
make build     # one-file binary  -> dist/autoclicker-<platform>
make onedir    # folder fallback  -> dist/autoclicker-<platform>-folder.zip
```

`.github/workflows/build.yml` runs the same script on Windows, macOS and Linux
and attaches the results to a GitHub release when a `v*` tag is pushed.

## License

MIT — see [LICENSE](LICENSE).
