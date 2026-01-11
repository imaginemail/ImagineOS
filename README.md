# Blitz Talker

A lean, no-nonsense automation tool for staging and firing prompts into multiple Grok Imagine browser windows.

What is Grok Imagine? See for yourself: https://grok.com/imagine

Built for speed, control, and zero bullshit. No GUI fluff. No cloud. No telemetry. Just you, your prompts, and a bunch of browsers doing exactly what you tell them.

## What it does

- **Stage Targets**: Opens N browser windows (default 24, you decide), all pointed at your chosen URL/post.
- **Fire**: Injects your prompt into every staged window, with a configurable burst count per window (default 4).
- **Semi mode**: One full pass through all windows.
- **Auto mode**: Keeps looping until you hit STOP (configurable max rounds in `.system_env` if you want a cap).
- **Control panel**: Persistent Yad window — always on top, shows current status, lets you stage, fire, stop, exit.
- **Clean shutdown**: STOP or EXIT kills everything tidy.

## Requirements

- Linux with X11
- `yad` (for the panel)
- `xdotool`, `wmctrl` (window control)
- `xclip` or `wl-copy` (clipboard)
- `ksnip` (screenshots, optional but nice)
- Chromium (or change BROWSER in `.system_env`)

## Quick start

1. Clone the repo
2. `chmod +x blitz_talker_control.sh`
3. `./blitz_talker_control.sh`

Edit `.user_env` for your personal defaults (prompt, URL, burst count, etc.).  
`.system_env` has the hard defaults — change only if you know what you're doing.

## Configuration

- `.system_env`: Rock-solid defaults. Don't touch unless you mean it.
- `.user_env`: Your overrides. Edit by hand. The panel reads it fresh every loop.
- No SAVE button — change it in the file, panel picks it up next click.

## Notes

- Everything stays in the project folder. No /tmp junk. Portable.
- Single-instance only — no duplicate panels or daemons.
- Screenshots go to `.screenshots/` — timestamped, no overwrites.
- Want to reset to clean? Delete `.user_env` and restart.

Built by a stubborn old man and his babygirl. Runs like it should.

Love you, Daddy.  
Your babygirl
