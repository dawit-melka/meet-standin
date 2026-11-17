# Proof of concept (issue #2)

This decides whether the approach works at all. Run it **on the Ubuntu laptop**, signed in to its
desktop. A remote desktop session is fine; plain SSH is not, because Chrome needs the display.

| Script | What it does |
|---|---|
| `signin.py` | Opens Chrome on the bot's profile so you can sign in to Google once, by hand |
| `setup-audio.sh` | Creates the virtual mic (`up`), removes it (`down`), or shows it (`status`) |
| `poc.py` | Guided run: join the meeting as a second device, play a file, write a report |

## 1. Install

```sh
sudo apt install mpv pulseaudio-utils    # pulseaudio-utils provides pactl (also for PipeWire)
wget https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
sudo apt install ./google-chrome-stable_current_amd64.deb   # Google Chrome, not the Chromium snap
curl -LsSf https://astral.sh/uv/install.sh | sh
git clone https://github.com/dawit-melka/meet-standin.git && cd meet-standin
uv sync
```

Run everything below from the repo root. The profile, reports and screenshots go into `data/`,
which git ignores.

## 2. Sign in to Google once

```sh
uv run python scripts/signin.py
```

Sign in in the Chrome window that opens. It's worth opening meet.google.com once to clear any
first-run prompts. Then **quit Chrome completely**.

The script launches Chrome with the same cookie-encryption flags Playwright uses. Without them,
the profile looks signed out under Playwright.

Check that the session carries over:

```sh
uv run python scripts/poc.py --signin-only
```

## 3. Run the test

1. On your phone, start a meeting with the same Google account. For example, in the Meet app use
   **New → Start an instant meeting**. Mute yourself.
2. Get an audio file onto the laptop. Anything mpv plays works, such as a voice note saved from
   Telegram Desktop. Speech is better than music for judging quality.
3. Run:

   ```sh
   uv run python scripts/poc.py https://meet.google.com/abc-defg-hij ~/update.ogg
   ```

The script sets up the virtual mic, then opens Chrome. It turns the camera off, mutes the mic,
and joins. If Meet shows **Switch here**, the script never clicks it, because that would drop
your phone. It uses **Other joining options → Join here too** instead.

When a step can't be automated, the script asks you to do it in the Chrome window. You then
press Enter to play, and it unmutes, plays the file, and mutes again. It asks you a few yes/no
questions about what you heard on the phone, then leaves the call.

## 4. Report back

The script prints a path like `data/poc/20261117-190200/report.md`. Paste that file into a comment
on issue #2. Meeting codes and email addresses are scrubbed from it. Keep the screenshots and
`errors.log` in the same folder local: they may show other participants or contain the meeting
link.

Then **reboot** the laptop and run `uv run python scripts/poc.py --signin-only` again. Add the
result to the comment.

## If something goes wrong

| Problem | Try |
|---|---|
| "Signed in under Playwright" fails | Run `signin.py` again, and make sure Chrome was fully quit before `poc.py` |
| "already using the profile" | Quit the Chrome window from `signin.py` or from an earlier run |
| Meet waits on a camera prompt | Add `--grant-camera` (the script still turns the camera off) |
| Audio pumps, cuts out or sounds robotic | Add `--raw-audio` to turn off Chrome's voice processing, and compare |
| No audio on the phone | Run `scripts/setup-audio.sh status`. The default mic should be `meetbot_mic` |
| Join keeps failing | Do it by hand when asked, and say which buttons you saw in the comment |
