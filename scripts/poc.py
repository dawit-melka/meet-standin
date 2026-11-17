"""Proof of concept for issue #2: join a Meet as a second device and play audio into it.

Run on the target laptop, from the repo root, with your phone already in the meeting (same
Google account, muted):

    uv run python scripts/poc.py https://meet.google.com/abc-defg-hij update.ogg
    uv run python scripts/poc.py --signin-only        # e.g. after a reboot

The run is guided: it automates each step and, where a selector misses, asks you to do that step
by hand in the Chrome window. It writes data/poc/<time>/report.md (no email, no meeting code) for
pasting into issue #2, plus local screenshots. See scripts/README.md.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from playwright.async_api import Locator, Page, async_playwright

from meet_standin.config import Paths
from meet_standin.meet.chrome import (
    PLAYWRIGHT_IGNORE_DEFAULT_ARGS,
    ChromeError,
    check_profile_free,
)

SCRIPTS = Path(__file__).resolve().parent
MEET_ORIGIN = "https://meet.google.com"
MEET_URL = re.compile(r"^https://meet\.google\.com/[a-z]{3}-[a-z]{4}-[a-z]{3}/?(\?.*)?$")
# The report is pasted into a public issue: scrub meeting codes and email addresses from it.
MEET_CODE = re.compile(r"\b[a-z]{3}-[a-z]{4}-[a-z]{3}\b")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
MOD = "Meta" if sys.platform == "darwin" else "Control"
LEAD_IN_SECONDS = 1.0

# Candidate selectors as (role, accessible name). The report records which ones matched;
# those seed meet/selectors.py in #6.
MIC_IS_ON = ("button", re.compile(r"^turn off microphone", re.I))
MIC_IS_OFF = ("button", re.compile(r"^turn on microphone", re.I))
CAM_IS_ON = ("button", re.compile(r"^turn off camera", re.I))
CAM_IS_OFF = ("button", re.compile(r"^turn on camera", re.I))
JOIN_NOW = ("button", re.compile(r"^join now$", re.I))
ASK_TO_JOIN = ("button", re.compile(r"^ask to join$", re.I))
SWITCH_HERE = ("button", re.compile(r"switch here", re.I))
OTHER_OPTIONS = ("button", re.compile(r"other (joining options|ways to join)", re.I))
JOIN_HERE_TOO = [
    ("menuitem", re.compile(r"join here too", re.I)),
    ("button", re.compile(r"join here too", re.I)),
    ("option", re.compile(r"join here too", re.I)),
]
LEAVE_CALL = ("button", re.compile(r"^leave call$", re.I))
GUEST_NAME = ("textbox", re.compile(r"your name", re.I))
WAITING_TEXT = re.compile(r"asking to join|ask to be let in|someone in the call", re.I)

# "Switch here" moves the call off your phone; Companion mode has no audio.
NEVER_CLICK = re.compile(r"switch here|companion", re.I)

TOGGLES = {"mic": (MIC_IS_ON, MIC_IS_OFF, "d"), "camera": (CAM_IS_ON, CAM_IS_OFF, "e")}

# Turns off Chrome's voice processing for Meet's mic (only with --raw-audio).
RAW_AUDIO_JS = """
(() => {
  const md = navigator.mediaDevices;
  if (!md || !md.getUserMedia) return;
  const original = md.getUserMedia.bind(md);
  const off = { echoCancellation: false, noiseSuppression: false, autoGainControl: false };
  md.getUserMedia = (constraints) => {
    if (constraints && constraints.audio) {
      const audio = constraints.audio === true ? {} : constraints.audio;
      constraints = { ...constraints, audio: { ...audio, ...off } };
    }
    return original(constraints);
  };
})();
"""

MANUAL_JOIN_HELP = """
Couldn't finish joining automatically. In the Chrome window:
  * If you see "Switch here", do NOT click it (it drops your phone). Open
    "Other joining options" and choose "Join here too".
  * Otherwise click "Join now" / "Ask to join".
  * Keep the microphone muted and the camera off.
"""


# --------------------------------------------------------------------------- report


@dataclass
class Report:
    started: datetime = field(default_factory=datetime.now)
    checks: list[tuple[str, str, str]] = field(default_factory=list)
    facts: list[tuple[str, str]] = field(default_factory=list)
    methods: list[tuple[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def check(self, name: str, ok: bool | None, detail: str = "") -> None:
        mark = {True: "pass", False: "FAIL", None: "?"}[ok]
        self.checks.append((name, mark, detail))
        print(f"  [{mark}] {name}" + (f": {detail}" if detail else ""))

    def fact(self, name: str, value: str) -> None:
        self.facts.append((name, value))
        print(f"  {name}: {value}")

    def method(self, step: str, how: str) -> None:
        self.methods.append((step, how))
        print(f"  {step}: {how}")

    def note(self, text: str) -> None:
        self.notes.append(text)
        print(f"  note: {text}")

    def markdown(self) -> str:
        def cell(s: str) -> str:
            return s.replace("|", "\\|").replace("\n", " ")

        out = [f"## PoC run {self.started:%Y-%m-%d %H:%M}", "", "| Check | Result | Detail |"]
        out.append("|---|---|---|")
        out += [f"| {cell(n)} | {r} | {cell(d)} |" for n, r, d in self.checks]
        out += ["", "### What worked for each step", ""]
        out += [f"- **{cell(s)}**: {cell(h)}" for s, h in self.methods] or ["- (none recorded)"]
        out += ["", "### Environment", ""]
        out += [f"- {n}: `{v}`" for n, v in self.facts]
        if self.notes:
            out += ["", "### Notes", ""] + [f"- {n}" for n in self.notes]
        return redact("\n".join(out) + "\n")


def redact(text: str) -> str:
    return EMAIL.sub("<email>", MEET_CODE.sub("<meeting-code>", text))


def log_error(out: Path, step: str, exc: BaseException) -> None:
    """Keep full error details local: Playwright errors can include URLs (the meeting code)."""
    with (out / "errors.log").open("a", encoding="utf-8") as f:
        f.write(f"--- {step}\n{''.join(traceback.format_exception(exc))}\n")
    print(f"  {step} failed: {exc}")


# --------------------------------------------------------------------------- helpers


async def ask(prompt: str) -> str:
    """input() on a daemon thread, so Ctrl+C at a prompt doesn't hang interpreter shutdown."""
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[str] = loop.create_future()

    def settle(result: str | None, error: BaseException | None) -> None:
        if fut.done():
            return
        if error is None:
            fut.set_result(result or "")
        else:
            fut.set_exception(error)

    def reader() -> None:
        try:
            line = input(prompt)
        except BaseException as e:
            loop.call_soon_threadsafe(settle, None, e)
        else:
            loop.call_soon_threadsafe(settle, line, None)

    threading.Thread(target=reader, daemon=True).start()
    return (await fut).strip()


async def ask_yes_no(question: str) -> bool | None:
    answer = (await ask(f"\n>>> {question} [y/n, Enter to skip] ")).lower()
    if answer.startswith("y"):
        return True
    if answer.startswith("n"):
        return False
    return None


async def find_visible(page: Page, *candidates: tuple[str, re.Pattern[str]]) -> Locator | None:
    for role, name in candidates:
        matches = page.get_by_role(role, name=name)  # type: ignore[arg-type]
        for i in range(await matches.count()):
            item = matches.nth(i)
            if await item.is_visible():
                return item
    return None


async def wait_visible(
    page: Page, candidates: list[tuple[str, re.Pattern[str]]], timeout: float
) -> Locator | None:
    deadline = time.monotonic() + timeout
    while True:
        found = await find_visible(page, *candidates)
        if found or time.monotonic() > deadline:
            return found
        await asyncio.sleep(0.5)


async def safe_click(loc: Locator) -> None:
    label = (await loc.get_attribute("aria-label")) or (await loc.inner_text())
    if NEVER_CLICK.search(label or ""):
        raise RuntimeError(f"refusing to click {label!r}")
    await loc.click(timeout=5000)


async def toggle_state(page: Page, which: str) -> bool | None:
    """True = on, False = off, None = couldn't tell."""
    is_on, is_off, _ = TOGGLES[which]
    if await find_visible(page, is_on):
        return True
    if await find_visible(page, is_off):
        return False
    return None


async def wait_state(page: Page, which: str, want: bool, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if await toggle_state(page, which) is want:
            return True
        await asyncio.sleep(0.25)
    return False


async def set_toggle(page: Page, which: str, on: bool, report: Report, where: str) -> bool:
    """Turn the mic or camera on/off: keyboard shortcut first, then the button. Verified."""
    step = f"{where}: {which} {'on' if on else 'off'}"
    state = await toggle_state(page, which)
    if state is None:
        report.note(f"{step}: couldn't read the {which} button state")
        return False
    if state is on:
        return True

    key = f"{MOD}+{TOGGLES[which][2]}"
    await page.keyboard.press(key)
    if await wait_state(page, which, on):
        report.method(step, f"shortcut {key}, verified by button label")
        return True

    is_on, is_off, _ = TOGGLES[which]
    button = await find_visible(page, is_on if state else is_off)
    if button:
        await button.click(timeout=5000)
        if await wait_state(page, which, on):
            report.method(step, "button click (shortcut didn't work), verified by button label")
            return True
    report.note(f"{step}: neither the shortcut nor the button worked")
    return False


# --------------------------------------------------------------------------- steps


async def record_environment(page: Page, args: argparse.Namespace, report: Report) -> None:
    ua = await page.evaluate("navigator.userAgent")
    chrome = re.search(r"Chrome/(\S+)", ua)
    report.fact("OS", platform.platform(terse=True))
    report.fact("Chrome", chrome.group(1) if chrome else ua)
    report.fact("Playwright", importlib.metadata.version("playwright"))
    report.fact("ignore_default_args", str(PLAYWRIGHT_IGNORE_DEFAULT_ARGS))
    report.fact("navigator.webdriver", str(await page.evaluate("navigator.webdriver")))
    report.fact("Raw audio (voice processing off)", str(args.raw_audio))
    report.fact("Permissions granted", ", ".join(permissions(args)))


async def check_signed_in(page: Page, report: Report) -> bool | None:
    """accounts.google.com redirects to myaccount.google.com only when signed in."""
    await page.goto("https://accounts.google.com/", wait_until="domcontentloaded")
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if urlparse(page.url).hostname == "myaccount.google.com":
            report.check("Signed in under Playwright", True, "accounts.google.com → myaccount")
            return True
        await asyncio.sleep(0.5)
    url = urlparse(page.url)
    report.check(
        "Signed in under Playwright",
        False,
        f"stayed on {url.hostname}{url.path}: signed out, or Google wants verification",
    )
    return False


async def join(page: Page, report: Report, admission_timeout: float) -> str | None:
    """Join from the pre-join screen. Returns how it joined, or None to fall back to manual."""
    here_too = await find_visible(page, *JOIN_HERE_TOO)
    switch = await find_visible(page, SWITCH_HERE)
    other = await find_visible(page, OTHER_OPTIONS)

    if here_too or switch or other:
        report.fact("Pre-join", "Meet saw this account already in the call")
        opened_menu = False
        if not here_too and other:
            await safe_click(other)
            opened_menu = True
            here_too = await wait_visible(page, JOIN_HERE_TOO, 5)
        if not here_too:
            report.note("Found the 'already in call' screen but not 'Join here too'")
            return None
        await safe_click(here_too)
        how = "Other joining options → Join here too" if opened_menu else "Join here too"
    else:
        report.note(
            "Meet did NOT offer 'Switch here' / 'Join here too'. Is your phone in this "
            "meeting with the same Google account?"
        )
        button = await find_visible(page, JOIN_NOW, ASK_TO_JOIN)
        if not button:
            return None
        how = (await button.inner_text()).strip() or "join button"
        await safe_click(button)

    report.method("Join", how)
    deadline = time.monotonic() + admission_timeout
    said_waiting = False
    while time.monotonic() < deadline:
        if await find_visible(page, LEAVE_CALL):
            return how
        if not said_waiting and await page.get_by_text(WAITING_TEXT).first.is_visible():
            report.note("Had to wait to be admitted")
            said_waiting = True
        await asyncio.sleep(1)
    report.note(f"Not in the call after {admission_timeout:.0f}s")
    return None


async def play(audio: Path, device: str, report: Report) -> None:
    await asyncio.sleep(LEAD_IN_SECONDS)  # the sink is silent, so this is the lead-in
    started = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        "mpv", "--no-video", "--no-terminal", f"--audio-device={device}", str(audio)
    )
    try:
        code = await proc.wait()
    finally:
        if proc.returncode is None:
            proc.terminate()
            await proc.wait()
    elapsed = time.monotonic() - started
    report.check("mpv played the file", code == 0, f"exit {code}, {elapsed:.0f}s")


async def meeting(page: Page, args: argparse.Namespace, report: Report, shots: Path) -> None:
    async def shot(name: str) -> None:
        await page.screenshot(path=shots / f"{name}.png")

    await page.goto(args.url, wait_until="domcontentloaded")
    await page.bring_to_front()
    ready = await wait_visible(
        page,
        [JOIN_NOW, ASK_TO_JOIN, SWITCH_HERE, OTHER_OPTIONS, *JOIN_HERE_TOO, GUEST_NAME],
        45,
    )
    await shot("1-prejoin")
    report.check("Screenshot of the Meet page", True, "page.screenshot()")
    if urlparse(page.url).hostname == "accounts.google.com":
        report.check("Meet opened signed in", False, "redirected to Google sign-in")
        return
    if ready and await find_visible(page, GUEST_NAME):
        report.check("Meet opened signed in", False, "Meet asks for a guest name")
        return

    if ready:
        off = await devices_off(page, args, report)
        if not off:
            await shot("1a-devices-not-off")
            await ask(
                ">>> Couldn't confirm the camera is off and the mic is muted. "
                "Fix that in the Chrome window, then press Enter... "
            )
            off = await devices_off(page, args, report)
        report.check("Camera off and mic muted before joining", off)
        if not off:
            report.note("Didn't join, because the camera and mic state couldn't be confirmed")
            return
    try:
        how = await join(page, report, args.admission_timeout) if ready else None
    except Exception as e:
        log_error(shots, "automatic join", e)
        report.note(f"Automatic join failed ({type(e).__name__}); details in local errors.log")
        how = None
    if how is None:
        await shot("1b-join-failed")
        print(MANUAL_JOIN_HELP)
        await ask(">>> Press Enter once the laptop is in the call... ")
        how = "manual" if await find_visible(page, LEAVE_CALL) else None
        if how:
            report.method("Join", "manual (automation failed, see notes)")
    report.check("Joined the call", how is not None, how or "")
    if how is None:
        return
    await shot("2-joined")

    mic = await toggle_state(page, "mic")
    report.check("Mic muted after joining", mic is False, "" if mic is not None else "unknown")
    if mic:
        await set_toggle(page, "mic", False, report, "in call")
    if await toggle_state(page, "camera"):
        await set_toggle(page, "camera", False, report, "in call")
    camera = await toggle_state(page, "camera")
    report.check("Camera off after joining", camera is False, "" if camera is not None else "?")
    if camera:
        report.note("Left the call: the camera was on and couldn't be turned off")
        await leave_call(page)
        return
    state = await page.evaluate(
        "navigator.permissions.query({name: 'microphone'}).then(p => p.state)"
    )
    report.fact("Mic permission state in page", state)
    report.check(
        "Phone still in the call after the laptop joined",
        await ask_yes_no("Look at your phone: are you still in the call?"),
        "answered by you",
    )

    await ask(f"\n>>> Press Enter to unmute and play {args.audio.name} (Ctrl+C aborts)... ")
    unmuted = await set_toggle(page, "mic", True, report, "in call")
    report.check("Unmute verified", unmuted)
    if unmuted:
        try:
            await play(args.audio, args.audio_device, report)
        finally:
            muted = await set_toggle(page, "mic", False, report, "in call")
            report.check("Muted again after playback", muted)
    await shot("3-after-playback")

    for name, question in [
        ("Audio clear on the phone", "Was the audio clear and easy to understand on your phone?"),
        ("First words not clipped", "Did you hear the very first words?"),
        (
            "No pumping, dropouts or robotic sound",
            "Was it free of pumping, dropouts or robotic sound?",
        ),
        ("Phone still in the call after playback", "Is your phone still in the call?"),
        (
            "No prompts or dialogs needed clicking",
            "Did it all run without you clicking any prompt?",
        ),
    ]:
        report.check(name, await ask_yes_no(question), "answered by you")

    await ask("\n>>> Press Enter to leave the call... ")
    left = await leave_call(page)
    if left:
        report.method("Leave", "button 'Leave call'")
    report.check("Left the call", left)
    await shot("4-left")


async def devices_off(page: Page, args: argparse.Namespace, report: Report) -> bool:
    """Camera off and mic muted, both verified from the button labels.

    Without the camera permission Meet can't send video, so only then does an unreadable
    camera button count as off.
    """
    camera_off = await set_toggle(page, "camera", False, report, "pre-join")
    if not camera_off and not args.grant_camera:
        camera_off = await toggle_state(page, "camera") is None
    mic_off = await set_toggle(page, "mic", False, report, "pre-join")
    return camera_off and mic_off


async def leave_call(page: Page) -> bool:
    leave = await find_visible(page, LEAVE_CALL)
    if leave:
        await leave.click(timeout=5000)
        await asyncio.sleep(3)
    return await find_visible(page, LEAVE_CALL) is None


def permissions(args: argparse.Namespace) -> list[str]:
    return ["microphone", "camera"] if args.grant_camera else ["microphone"]


async def run(args: argparse.Namespace, paths: Paths, report: Report, shots: Path) -> None:
    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            str(paths.profile_dir),
            channel="chrome",
            headless=False,
            ignore_default_args=PLAYWRIGHT_IGNORE_DEFAULT_ARGS,
            no_viewport=True,
        )
        try:
            if args.raw_audio:
                await ctx.add_init_script(RAW_AUDIO_JS)
            await ctx.grant_permissions(permissions(args), origin=MEET_ORIGIN)
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            await record_environment(page, args, report)
            if await check_signed_in(page, report) and not args.signin_only:
                await meeting(page, args, report, shots)
        finally:
            try:
                await ctx.close()
            except Exception as e:  # Ctrl+C may already have closed Chrome
                print(f"  (closing Chrome: {e})")


# --------------------------------------------------------------------------- setup


def preflight(args: argparse.Namespace, paths: Paths, report: Report) -> str | None:
    """Returns an error message, or None if the run can start."""
    if not (paths.profile_dir.exists() and any(paths.profile_dir.iterdir())):
        return f"No Chrome profile at {paths.profile_dir}. Run: uv run python scripts/signin.py"
    try:
        check_profile_free(paths.profile_dir)
    except ChromeError as e:
        return str(e)
    if args.signin_only:
        return None

    if not args.url or not MEET_URL.match(args.url):
        return "Give a Meet link like https://meet.google.com/abc-defg-hij"
    if not args.audio or not args.audio.is_file():
        return "Give an audio file to play (anything mpv plays, e.g. a Telegram voice note)."
    if not shutil.which("mpv"):
        return "mpv is not installed. On Ubuntu: sudo apt install mpv"

    if sys.platform == "darwin":
        if args.audio_device.startswith("pulse/"):
            return (
                "On macOS, pass --audio-device coreaudio/<BlackHole id> "
                "(list devices with: mpv --audio-device=help)"
            )
    elif not args.skip_audio_setup:
        print("Setting up the virtual mic:")
        result = subprocess.run([SCRIPTS / "setup-audio.sh", "up"], check=False)
        report.check("Virtual mic set up", result.returncode == 0, "scripts/setup-audio.sh up")
        if result.returncode != 0:
            return "Virtual mic setup failed (see above)."
    return None


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("url", nargs="?", help="Meet link, e.g. https://meet.google.com/abc-defg-hij")
    p.add_argument("audio", nargs="?", type=Path, help="audio file to play into the meeting")
    p.add_argument("--signin-only", action="store_true", help="only check the Google sign-in")
    p.add_argument(
        "--audio-device",
        default="pulse/meetbot_sink",
        help="mpv --audio-device to play into (default: %(default)s)",
    )
    p.add_argument("--skip-audio-setup", action="store_true", help="don't run setup-audio.sh")
    p.add_argument(
        "--raw-audio",
        action="store_true",
        help="turn off Chrome's noise suppression, auto gain and echo cancellation for Meet",
    )
    p.add_argument(
        "--grant-camera",
        action="store_true",
        help="also grant the camera permission (try this if Meet blocks on a camera prompt)",
    )
    p.add_argument(
        "--admission-timeout",
        type=float,
        default=120,
        help="seconds to wait to be in the call after clicking join (default: %(default)s)",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    load_dotenv(Path.cwd() / ".env")
    paths = Paths.from_env()
    report = Report()

    error = preflight(args, paths, report)
    if error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    out = paths.data_dir / "poc" / f"{report.started:%Y%m%d-%H%M%S}"
    out.mkdir(parents=True, exist_ok=True)
    interrupted = False
    try:
        asyncio.run(run(args, paths, report, out))
    except KeyboardInterrupt:
        interrupted = True
        report.note("Run aborted with Ctrl+C")
    finally:
        (out / "report.md").write_text(report.markdown(), encoding="utf-8")
        print(f"\nReport: {out / 'report.md'}")
        print(f"Screenshots and errors.log: {out} (local only; may show participants)")
        print("Paste the report into a comment on issue #2.")
    if interrupted:
        return 130  # conventional exit status for SIGINT
    failed = any(result == "FAIL" for _, result, _ in report.checks)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
