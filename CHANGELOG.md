# Changelog

Every pull request into `master` adds a section on top, with a version newer
than the last one, and bumps `src/autoclicker/version.py` to match. Merging it
releases that version: the tag, the GitHub release with this text, and the
itch.io upload are all made by CI. A pull request that should not release
anything (docs, CI) gets the `no release` label and leaves this file alone.

## 1.1.0

### Added
- **Working hours** for Stay online: time ranges such as
  `09:00-12:00, 13:00-18:00` on days such as `mon-fri`. Outside them it waits
  and shows when it resumes; it keeps working after the machine sleeps.
- **Start with Windows** (and macOS, Linux): come up in the tray at login and
  stay online on your working hours. A switch on the Stay online tab, or
  `autoclicker --autostart on|off|status`. No admin rights needed.

## 1.0.0

First release of the terminal version: auto clicker with idle mode and
auto-stop timer, Stay online, global hotkeys, profiles, and background mode
with a tray icon.
