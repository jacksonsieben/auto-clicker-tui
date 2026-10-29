# itch.io page

The text and settings for <https://jacksonsieben.itch.io/stay-online-auto-clicker>.
Paste from here so the page and the repository stay in step.

## Setup (once)

1. Create the project at <https://itch.io/game/new>.
   - **Title:** Stay Online — Auto Clicker & Away-Status Blocker
   - **Project URL:** `stay-online-auto-clicker`
   - **Short description:** Keep Teams and Slack on *Available*, and click
     automatically. No install, no admin rights.
   - **Classification:** Tools
   - **Kind of project:** Downloadable
   - **Pricing:** Paid — `$4.99`, with *Let people pay more than the minimum*
     ticked
   - **Uploads:** none by hand. CI pushes them (step 3).
   - **Genre / tags:** `auto-clicker`, `mouse-jiggler`, `microsoft-teams`,
     `slack`, `productivity`, `automation`, `windows`, `utility`
   - **Visibility:** Draft until the first CI push lands, then Public.
2. Create an API key at <https://itch.io/user/settings/api-keys>.
3. In GitHub, *Settings → Secrets and variables → Actions*:
   - secret `BUTLER_API_KEY` = the key from step 2
   - variable `ITCH_TARGET` = `jacksonsieben/stay-online-auto-clicker`
4. Push a `v*` tag. The release job uploads the Windows, Windows portable,
   macOS and Linux builds as separate channels; itch.io marks the platforms
   from the channel names.
5. Payouts: *Settings → Payouts* (PayPal or Stripe), and the tax interview
   itch.io asks for before the first payout.

Revenue share is set under *Settings → Revenue sharing*; the default is 10%.

## Page text

> **Stop showing as *Away* when you're not.**
>
> Stay Online keeps Microsoft Teams, Slack and the rest on *Available* while
> you read, think or step away for a minute. It is also a full auto clicker,
> with global hotkeys, and it is careful: it stops by itself when you lock or
> suspend the machine.
>
> **No install, no admin rights.** One file. Download it, run it. It works
> on locked-down work laptops where you can't install software, and there
> is a portable folder build for machines that block single `.exe` files.
>
> ### Stay online
> - Keeps your status on *Available* by tapping a key no app uses (`F15`):
>   your cursor never moves and nothing gets typed
> - Auto-stop timer: stay online for the next 2 hours, then stop
> - `F8` toggles it from any window
>
> ### Auto clicker
> - Interval from 1 ms to hours
> - Left, right or middle button
> - Click forever or a fixed number of times
> - Click where the cursor is, or at a fixed spot you pick
> - **Idle mode:** only clicks while you're away from the keyboard
> - Global hotkeys `F6` / `F7`, rebindable
>
> ### Runs out of the way
> - Background mode with a tray icon on Windows
> - Named profiles; your last session comes back on the next start
>
> ### Open source
> The code is MIT-licensed on
> [GitHub](https://github.com/jacksonsieben/auto-clicker-tui), so you can read
> exactly what it does. Buying the ready-made build pays for its upkeep.
>
> **Platforms:** Windows 10/11, macOS, Linux (X11).
>
> *Check your workplace's rules before you use it there.*

## Screenshots

itch.io wants at least one and shows up to five. Take them on Windows, at a
readable terminal size:

1. The *Stay online* tab, running, with the auto-stop timer set
2. The *Clicking* tab, running
3. The tray icon menu
4. The *Profiles* tab
