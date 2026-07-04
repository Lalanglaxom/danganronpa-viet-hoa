from __future__ import annotations

import html
import os
import platform
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from .cancel import StopFn, check_stop, sleep_with_stop
from .cleanup import RenameChange, normalize_duplicate_names
from .discovery import find_backup_for_file, iter_po_files
from .models import POEntry
from .po_io import format_field, load_po, patch_msgstr_by_uid, po_unescape_quoted, save_po
from .translator import TranslationError, validate_translations

STOP_BTN_SEL = (
    'button[aria-label*="Stop"], button[aria-label*="Dừng"], '
    'button:has(mat-icon[fonticon="stop"]), button:has(mat-icon[data-mat-icon-name="stop"])'
)
CHATBOX_SEL = (
    'div.ql-editor[contenteditable="true"][aria-label*="Enter a prompt for Gemini"], '
    'div.ql-editor[contenteditable="true"][data-placeholder*="Ask Gemini"], '
    'rich-textarea div.ql-editor[contenteditable="true"], '
    '[data-test-id="textarea-inner"] div[contenteditable="true"], '
    '[role="textbox"][contenteditable="true"]'
)
SEND_BTN_SEL = (
    'div[data-test-id="send-button-container"] button[aria-label="Send message"], '
    'gem-icon-button.send-button button[aria-label="Send message"], '
    'button[aria-label="Send message"], '
    'div[data-test-id="send-button-container"] button'
)
RESPONSE_MARKDOWN_SEL = 'div.markdown-main-panel, message-content, pre'
DEFAULT_CDP_URL = "http://localhost:9222"
DEFAULT_GEMINI_URL = "https://gemini.google.com/app"
DEFAULT_CHROME_USER_DATA_DIR = str(Path.home() / "ChromeDebug")
DEFAULT_MAX_ENTRIES_PER_BATCH = 40
DEFAULT_BATCH_RETRIES = 2
DEFAULT_NO_PROGRESS_TIMEOUT_SECONDS = 60


TRANSLATE_PROMPT_TEMPLATE = """Translate ONLY the .po entries included below for the Danganronpa project into Vietnamese.
Use my Saved Information for character-specific tones and terminology if available.
Ignore all earlier chat context and earlier files. Use ONLY the INPUT ENTRIES in this prompt.

OUTPUT FORMAT — follow exactly:
- Return one single code block only.
- For each input entry, output exactly two fields: msgctxt and msgstr.
- Do not output msgid.
- Return every requested msgctxt exactly once, in the same order.
- Do not add explanations, markdown text, summaries, or comments outside the code block.

EXAMPLE OUTPUT:
```po
msgctxt "0003 | MAKOTO NAEGI"
msgstr "Tôi hy vọng chúng ta có thể hòa thuận!"
```

TRANSLATION RULES:
* Translate into Vietnamese only.
* Tags are protected in the input as tokens like ⟦CLT X⟧ and ⟦CLT⟧. Preserve those tokens exactly.
* The English msgid is the source of truth. Translate by following the English meaning, wording, order, and intent as closely as natural Vietnamese allows.
* Use Japanese #. comment lines only as secondary context for speaker tone, ambiguity, or terminology. Never let Japanese context override, expand, shorten, or change the English source.
* Preserve placeholders and all other symbols exactly.
* Never leave msgstr empty.
* Do not translate speaker names or msgctxt IDs.

INPUT ENTRIES:

{entries}"""

SUMMARY_PROMPT = """Based on these Danganronpa dialogue lines, give me a 2-3 word English label suitable for a folder name.
Examples: Sayaka Door Scare, Class Trial Vote, Dining Hall Talk.
Reply with ONLY those 2-3 words. No quotes. No punctuation except spaces.

Lines:
{samples}"""

LogFn = Callable[[str], None]
AllowInvalid = bool | Callable[[], bool]


def _allow_invalid_enabled(value: AllowInvalid) -> bool:
    if callable(value):
        try:
            return bool(value())
        except Exception:
            return False
    return bool(value)


@dataclass(slots=True)
class WebBatchResult:
    batch_index: int
    batch_total: int
    requested: int
    parsed: int
    accepted: int
    errors: list[TranslationError] = field(default_factory=list)


@dataclass(slots=True)
class WebTranslateFileResult:
    file: Path
    missing_before: int
    total_entries: int
    translated: int
    errors: list[TranslationError] = field(default_factory=list)
    batches: list[WebBatchResult] = field(default_factory=list)
    debug_log: Path | None = None
    backup_created: bool = False
    folder_renamed_from: Path | None = None
    folder_renamed_to: Path | None = None
    folder_rename_skipped_reason: str = ""


@dataclass(slots=True)
class WebTranslateRunResult:
    renamed_duplicates: list[RenameChange]
    files: list[WebTranslateFileResult]

    @property
    def total_translated(self) -> int:
        return sum(item.translated for item in self.files)

    @property
    def total_errors(self) -> int:
        return sum(len(item.errors) for item in self.files)


ANGLE_TAG_RE = re.compile(r"<([^<>\n]{1,80})>")
SAFE_TAG_RE = re.compile(r"⟦([^⟦⟧\n]{1,80})⟧")


def encode_angle_tags_for_prompt(text: str) -> str:
    """Protect angle-bracket game tags before pasting into Gemini Web.

    Gemini's rich text editor can treat strings like <CLT 4> as HTML when
    content is inserted programmatically. That can blank out the pasted PO body.
    The prompt therefore uses visible safe tokens and the parser decodes them
    back before validation/writing.
    """
    return ANGLE_TAG_RE.sub(lambda m: f"⟦{m.group(1)}⟧", text)


def decode_angle_tags_from_response(text: str) -> str:
    return SAFE_TAG_RE.sub(lambda m: f"<{m.group(1)}>", text)


def _po_raw_to_text(raw_block: str) -> str:
    parts = re.findall(r'"((?:[^"\\]|\\.)*)"', raw_block)
    text = "".join(parts)
    # Reuse the safer PO unescaper line by line when possible.
    try:
        decoded = []
        for quoted in re.findall(r'"(?:[^"\\]|\\.)*"', raw_block):
            decoded.append(po_unescape_quoted(quoted))
        return "".join(decoded)
    except Exception:
        return text.replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")


def entry_to_po_prompt_block(entry: POEntry) -> str:
    comments = [line for line in entry.comments if line.startswith("#.")]
    parts: list[str] = []
    parts.extend(comments)
    if entry.msgctxt is not None:
        parts.append(format_field("msgctxt", entry.msgctxt))
    parts.append(format_field("msgid", entry.msgid))
    parts.append('msgstr ""')
    return encode_angle_tags_for_prompt("\n".join(parts) + "\n")


def batch_entries_by_lines(
    entries: Iterable[POEntry],
    max_lines_per_batch: int = 600,
    max_entries_per_batch: int = DEFAULT_MAX_ENTRIES_PER_BATCH,
) -> list[list[POEntry]]:
    batches: list[list[POEntry]] = []
    current: list[POEntry] = []
    current_lines = 0
    line_limit = max(20, int(max_lines_per_batch))
    entry_limit = max(1, int(max_entries_per_batch))

    for entry in entries:
        line_estimate = max(3, entry.msgid.count("\n") + len(entry.comments) + 3)
        if current and (current_lines + line_estimate > line_limit or len(current) >= entry_limit):
            batches.append(current)
            current = []
            current_lines = 0
        current.append(entry)
        current_lines += line_estimate

    if current:
        batches.append(current)
    return batches


def parse_translated_po_response(response_text: str) -> dict[str, str]:
    """Parse Gemini Web PO-style output.

    Supported Gemini outputs:
    - preferred: msgctxt + msgstr only
    - legacy full entry: msgctxt + msgid + msgstr
    - inverted mistake: msgctxt + translated msgid + empty msgstr
    - HTML-escaped tags from Gemini code DOM, e.g. &lt;CLT 4&gt;
    """
    text = html.unescape(response_text).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"</?(?:code|pre)[^>]*>", "", text, flags=re.IGNORECASE)

    fenced = re.findall(r"```[a-zA-Z0-9_-]*\s*(.*?)\s*```", text, re.DOTALL)
    content = "\n\n".join(fenced) if fenced else text

    q = r'"(?:[^"\\]|\\.)*"'
    quoted_lines = q + r'(?:\s*\n\s*' + q + r')*'
    entry_pat = re.compile(
        r'^\s*msgctxt\s+(?P<ctx>' + q + r')\s*\n'
        r'(?:\s*msgid\s+(?P<msgid>' + quoted_lines + r')\s*\n)?'
        r'\s*msgstr\s+(?P<msgstr>' + quoted_lines + r')',
        re.MULTILINE,
    )

    translations: dict[str, str] = {}
    for match in entry_pat.finditer(content):
        try:
            ctx = po_unescape_quoted(match.group("ctx"))
        except Exception:
            continue

        msgstr_raw = match.group("msgstr") or '""'
        msgid_raw = match.group("msgid") or '""'
        msgstr = decode_angle_tags_from_response(html.unescape(_po_raw_to_text(msgstr_raw)))
        msgid = decode_angle_tags_from_response(html.unescape(_po_raw_to_text(msgid_raw)))

        if msgstr.strip():
            translations[ctx] = msgstr
        elif msgid.strip():
            translations[ctx] = msgid

    return translations

def _response_locator(page):
    """Return Gemini response containers for both old and current Gemini DOMs."""
    for selector in (
        "model-response",
        "message-content",
        'div[id^="model-response-message-content"]',
    ):
        try:
            loc = page.locator(selector)
            if loc.count() > 0:
                return loc
        except Exception:
            pass
    return page.locator("model-response")


def _count_responses(page) -> int:
    try:
        return _response_locator(page).count()
    except Exception:
        return 0


def _extract_nth_response(page, index: int) -> str:
    """Fast response text extraction. Avoid slow nested Playwright locator timeouts."""
    try:
        return str(page.evaluate(
            """(idx) => {
                const roots = [
                    ...document.querySelectorAll('model-response'),
                    ...document.querySelectorAll('message-content'),
                    ...document.querySelectorAll('div[id^="model-response-message-content"]')
                ];
                const seen = new Set();
                const unique = [];
                for (const el of roots) {
                    if (!seen.has(el)) { seen.add(el); unique.push(el); }
                }
                if (!unique.length) return '';
                const root = unique[idx] || unique[unique.length - 1];
                const code = root.querySelector('code[data-test-id="code-content"], pre code, pre');
                if (code && (code.innerText || code.textContent)) return code.innerText || code.textContent || '';
                const md = root.querySelector('div.markdown-main-panel, div[id^="model-response-message-content"]');
                if (md && (md.innerText || md.textContent)) return md.innerText || md.textContent || '';
                return root.innerText || root.textContent || '';
            }""",
            index,
        ) or "")
    except Exception:
        try:
            all_resps = _response_locator(page)
            total = all_resps.count()
            if total == 0:
                return ""
            resp = all_resps.nth(index) if index < total else all_resps.last
            return resp.inner_text(timeout=1_500)
        except Exception:
            return ""


def _visible_stop_button_count(page) -> int:
    """Count only visible, enabled Gemini stop buttons.

    Gemini's current UI can leave a raw mat-icon like
    <mat-icon fonticon="stop" aria-hidden="true"> in the DOM. That icon alone
    must not be treated as an active generation state. It counts only when it is
    inside a visible, clickable, enabled button.
    """
    try:
        return int(page.evaluate(
            """() => {
                const isVisible = (el) => {
                    if (!el || el.hidden) return false;
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0
                        && rect.height > 0
                        && style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && style.opacity !== '0';
                };
                const isDisabled = (btn) => {
                    return btn.disabled
                        || btn.hasAttribute('disabled')
                        || btn.getAttribute('aria-disabled') === 'true'
                        || btn.closest('[aria-disabled="true"]');
                };
                const isStopButton = (btn) => {
                    const label = (btn.getAttribute('aria-label') || '').toLowerCase();
                    const title = (btn.getAttribute('title') || '').toLowerCase();
                    if (label.includes('stop') || label.includes('dừng') || title.includes('stop')) return true;
                    const icon = btn.querySelector('mat-icon[fonticon="stop"], mat-icon[data-mat-icon-name="stop"]');
                    if (!icon || !isVisible(icon)) return false;
                    const fonticon = (icon.getAttribute('fonticon') || '').toLowerCase();
                    const iconName = (icon.getAttribute('data-mat-icon-name') || '').toLowerCase();
                    const text = (icon.textContent || '').trim().toLowerCase();
                    return fonticon === 'stop' || iconName === 'stop' || text === 'stop';
                };
                return Array.from(document.querySelectorAll('button'))
                    .filter((btn) => isVisible(btn) && !isDisabled(btn) && isStopButton(btn))
                    .length;
            }"""
        ) or 0)
    except Exception:
        try:
            return int(page.locator(STOP_BTN_SEL).count())
        except Exception:
            return 0


def _click_stop_button(page) -> bool:
    try:
        return bool(page.evaluate(
            """() => {
                const isVisible = (el) => {
                    if (!el || el.hidden) return false;
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0
                        && rect.height > 0
                        && style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && style.opacity !== '0';
                };
                const isDisabled = (btn) => {
                    return btn.disabled
                        || btn.hasAttribute('disabled')
                        || btn.getAttribute('aria-disabled') === 'true'
                        || btn.closest('[aria-disabled="true"]');
                };
                const isStopButton = (btn) => {
                    const label = (btn.getAttribute('aria-label') || '').toLowerCase();
                    const title = (btn.getAttribute('title') || '').toLowerCase();
                    if (label.includes('stop') || label.includes('dừng') || title.includes('stop')) return true;
                    if (label.includes('cancel') || label.includes('hủy')) return true;
                    const icon = btn.querySelector('mat-icon[fonticon="stop"], mat-icon[data-mat-icon-name="stop"]');
                    if (!icon || !isVisible(icon)) return false;
                    const fonticon = (icon.getAttribute('fonticon') || '').toLowerCase();
                    const iconName = (icon.getAttribute('data-mat-icon-name') || '').toLowerCase();
                    const text = (icon.textContent || '').trim().toLowerCase();
                    return fonticon === 'stop' || iconName === 'stop' || text === 'stop';
                };
                const btn = Array.from(document.querySelectorAll('button'))
                    .find((candidate) => isVisible(candidate) && !isDisabled(candidate) && isStopButton(candidate));
                if (!btn) return false;
                btn.click();
                return true;
            }"""
        ))
    except Exception:
        try:
            btn = page.locator(STOP_BTN_SEL).last
            if btn.count() > 0:
                btn.click(timeout=2_000, force=True)
                return True
        except Exception:
            pass
    return False



def _has_unclickable_stop_box(page) -> bool:
    """Detect Gemini's stuck mobile composer state.

    Bad DOM example:
    - send-button-container is visible but disabled
    - gem-icon-button has class "stop" and aria-disabled="true"
    - inner button says aria-label="Stop response"
    - mat-icon has fonticon="stop"

    In this state Gemini is neither truly generating nor ready to send. Clicking
    it does nothing, so the safest recovery is a page refresh and batch retry.
    """
    try:
        return bool(page.evaluate(
            """() => {
                const isVisible = (el) => {
                    if (!el || el.hidden) return false;
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0
                        && rect.height > 0
                        && style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && style.opacity !== '0';
                };
                const hasStopIcon = (root) => {
                    if (!root) return false;
                    const icon = root.querySelector('mat-icon[fonticon="stop"], mat-icon[data-mat-icon-name="stop"]');
                    if (!icon || !isVisible(icon)) return false;
                    const fonticon = (icon.getAttribute('fonticon') || '').toLowerCase();
                    const iconName = (icon.getAttribute('data-mat-icon-name') || '').toLowerCase();
                    const text = (icon.textContent || '').trim().toLowerCase();
                    return fonticon === 'stop' || iconName === 'stop' || text === 'stop';
                };
                const looksDisabled = (root) => {
                    if (!root) return false;
                    if (root.classList?.contains('disabled')) return true;
                    if (root.getAttribute('aria-disabled') === 'true') return true;
                    const gemBtn = root.querySelector('gem-icon-button[aria-disabled="true"], gem-icon-button.disabled');
                    const button = root.querySelector('button');
                    return !!gemBtn
                        || !!button?.disabled
                        || button?.hasAttribute('disabled')
                        || button?.getAttribute('aria-disabled') === 'true';
                };
                const containers = Array.from(document.querySelectorAll(
                    'div[data-test-id="send-button-container"], gem-icon-button.send-button'
                ));
                return containers.some((container) => {
                    if (!isVisible(container)) return false;
                    const label = (
                        container.getAttribute('aria-label')
                        || container.querySelector('button')?.getAttribute('aria-label')
                        || ''
                    ).toLowerCase();
                    const classText = String(container.className || '').toLowerCase();
                    const stopLike = label.includes('stop')
                        || label.includes('dừng')
                        || classText.split(/\\s+/).includes('stop')
                        || hasStopIcon(container);
                    return stopLike && looksDisabled(container);
                });
            }"""
        ))
    except Exception:
        return False


def _wait_for_chatbox_ready(page, timeout_ms: int = 30000) -> None:
    deadline = time.time() + (max(1, int(timeout_ms)) / 1000.0)
    while time.time() < deadline:
        try:
            loc = page.locator(CHATBOX_SEL)
            if loc.count() > 0 and loc.last.is_visible(timeout=800):
                return
        except Exception:
            pass
        try:
            page.wait_for_timeout(300)
        except Exception:
            time.sleep(0.3)


def _refresh_gemini_page(page) -> None:
    """Force-refresh Gemini and wait until the composer is visible again."""
    try:
        page.reload(wait_until="domcontentloaded", timeout=60_000)
    except Exception:
        try:
            page.goto(DEFAULT_GEMINI_URL, wait_until="domcontentloaded", timeout=60_000)
        except Exception:
            pass
    try:
        _wait_for_chatbox_ready(page, timeout_ms=30_000)
    except Exception:
        pass


def _refresh_page_for_unclickable_stop_box(page) -> bool:
    """Refresh Gemini when its disabled Stop response button traps the composer."""
    if not _has_unclickable_stop_box(page):
        return False
    _refresh_gemini_page(page)
    return True


def _wait_for_generation_to_finish(page, response_index: int, max_wait_seconds: int = 180, stop_requested: StopFn | None = None) -> str:
    """Wait for a new Gemini response without hanging forever.

    Safeguards:
    - real wall-clock deadline, not loop count
    - no-progress timeout when Gemini never produces text
    - fast DOM extraction so one check cannot stall for many seconds
    - click Stop before raising timeout
    """
    max_wait_seconds = max(30, int(max_wait_seconds))
    no_progress_timeout = min(DEFAULT_NO_PROGRESS_TIMEOUT_SECONDS, max(25, max_wait_seconds // 2))
    deadline = time.time() + max_wait_seconds
    no_progress_deadline = time.time() + no_progress_timeout
    last_text = ""
    last_change = time.time()
    stable_seconds = 0.0

    while time.time() < deadline:
        if stop_requested is not None and stop_requested():
            _click_stop_button(page)
            check_stop(stop_requested)

        if _refresh_page_for_unclickable_stop_box(page):
            raise TimeoutError("Gemini UI got stuck on a disabled Stop response box. Refreshed the page; batch will retry.")

        current_text = _extract_nth_response(page, response_index).strip()
        stop_count = _visible_stop_button_count(page)

        now = time.time()
        if current_text:
            no_progress_deadline = now + no_progress_timeout
            if current_text == last_text:
                stable_seconds += 1.0
                # When Gemini has no Stop button and text is stable, response is done.
                if stop_count == 0 and stable_seconds >= 6.0:
                    return current_text
            else:
                last_text = current_text
                last_change = now
                stable_seconds = 0.0
        elif now >= no_progress_deadline:
            _click_stop_button(page)
            raise TimeoutError("Gemini response did not start. The tab may have lost focus, send failed, or Gemini UI changed.")

        # Text has not changed for too long while a stop button remains: treat as stuck.
        if last_text and stop_count > 0 and now - last_change > no_progress_timeout:
            _click_stop_button(page)
            raise TimeoutError("Gemini response stopped making progress. Stopped generation so this batch can retry.")

        sleep_with_stop(1, stop_requested)

    _click_stop_button(page)
    if last_text:
        raise TimeoutError("Gemini response timeout after partial output. Batch will retry instead of hanging.")
    raise TimeoutError("Gemini response timeout before any output. Batch will retry instead of hanging.")


def _chrome_candidates() -> list[str]:
    system = platform.system().lower()
    candidates: list[str] = []
    if system == "windows":
        for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            base = os.environ.get(key)
            if base:
                candidates.append(str(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"))
        candidates.extend(["chrome.exe", "chrome"])
    elif system == "darwin":
        candidates.extend([
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            str(Path.home() / "Applications" / "Google Chrome.app" / "Contents" / "MacOS" / "Google Chrome"),
            "google-chrome",
            "chrome",
        ])
    else:
        candidates.extend(["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome"])
    return candidates


def find_chrome_executable() -> str:
    for candidate in _chrome_candidates():
        p = Path(candidate)
        if p.exists():
            return str(p)
        found = shutil.which(candidate)
        if found:
            return found
    raise RuntimeError("Cannot find Chrome. Install Google Chrome or add chrome.exe to PATH.")


def _cdp_port(cdp_url: str) -> str:
    m = re.search(r":(\d+)(?:/)?$", cdp_url.strip())
    return m.group(1) if m else "9222"


def open_chrome_debug(
    cdp_url: str = DEFAULT_CDP_URL,
    user_data_dir: str | Path = DEFAULT_CHROME_USER_DATA_DIR,
    url: str = DEFAULT_GEMINI_URL,
) -> list[str]:
    """Open a separate Chrome profile with remote debugging enabled.

    This does not touch PO files and does not close any existing browser.
    The caller can then log in to Gemini manually and run the web translator.
    """
    chrome = find_chrome_executable()
    profile = Path(user_data_dir).expanduser()
    profile.mkdir(parents=True, exist_ok=True)
    cmd = [
        chrome,
        f"--remote-debugging-port={_cdp_port(cdp_url)}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        url,
    ]
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return cmd


class GeminiWebSession:
    """Control the user's logged-in Gemini web tab through Chrome CDP.

    Start Chrome first, for example on Windows:
    chrome.exe --remote-debugging-port=9222 --user-data-dir="C:\\ChromeDebug"
    Then open Gemini and log in manually.
    """

    def __init__(self, cdp_url: str = DEFAULT_CDP_URL):
        self.cdp_url = cdp_url
        self._playwright = None
        self._browser = None
        self.page = None

    def __enter__(self) -> "GeminiWebSession":
        try:
            from playwright.sync_api import sync_playwright  # type: ignore
        except Exception as exc:
            raise RuntimeError("Install Playwright first: pip install playwright") from exc

        self._playwright = sync_playwright().start()
        try:
            self._browser = self._playwright.chromium.connect_over_cdp(self.cdp_url)
        except Exception as exc:
            self._playwright.stop()
            raise RuntimeError(
                "Cannot connect to Chrome. Start Chrome with: "
                'chrome.exe --remote-debugging-port=9222 --user-data-dir="C:\\ChromeDebug"'
            ) from exc

        contexts = self._browser.contexts
        context = contexts[0] if contexts else self._browser.new_context()
        pages = list(context.pages)

        gemini_pages = [p for p in pages if "gemini.google.com" in (p.url or "")]
        if not gemini_pages:
            raise RuntimeError("No Gemini tab found. Click Open Chrome, log in to Gemini, then run Gemini Web.")
        self.page = gemini_pages[-1]
        try:
            self.page.bring_to_front()
        except Exception:
            pass

        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        # Do not close the user's Chrome. Only stop the Playwright client.
        if self._playwright is not None:
            self._playwright.stop()

    def _ensure_page(self):
        if self._browser is None:
            raise RuntimeError("GeminiWebSession is not connected")
        try:
            if self.page is not None and not self.page.is_closed() and "gemini.google.com" in (self.page.url or ""):
                self.page.bring_to_front()
                return self.page
        except Exception:
            pass
        for context in self._browser.contexts:
            for page in context.pages:
                try:
                    if not page.is_closed() and "gemini.google.com" in (page.url or ""):
                        self.page = page
                        self.page.bring_to_front()
                        return self.page
                except Exception:
                    continue
        raise RuntimeError("Gemini tab lost. Reopen Gemini in the debug Chrome window, then run again.")

    def recover_after_error(self) -> None:
        try:
            page = self._ensure_page()
            if _refresh_page_for_unclickable_stop_box(page):
                return
            _click_stop_button(page)
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
            if _refresh_page_for_unclickable_stop_box(page):
                return
            try:
                box = _active_chatbox(page)
                _clear_chatbox(page, box)
            except Exception:
                pass
        except Exception:
            pass

    def refresh_page(self) -> None:
        """Force-refresh Gemini after normal retries are exhausted."""
        try:
            page = self._ensure_page()
            _refresh_gemini_page(page)
        except Exception:
            pass

    def send(self, text: str, max_wait_seconds: int = 180, stop_requested: StopFn | None = None) -> str:
        page = self._ensure_page()
        return send_to_gemini(page, text, max_wait_seconds=max_wait_seconds, stop_requested=stop_requested)


def _active_chatbox(page):
    for selector in (
        'div.ql-editor[contenteditable="true"][aria-label*="Enter a prompt for Gemini"]',
        'div.ql-editor[contenteditable="true"][data-placeholder*="Ask Gemini"]',
        'rich-textarea div.ql-editor[contenteditable="true"]',
        '[data-test-id="textarea-inner"] div[contenteditable="true"]',
        '[role="textbox"][contenteditable="true"]',
    ):
        try:
            loc = page.locator(selector)
            if loc.count() > 0:
                return loc.last
        except Exception:
            pass

    boxes = page.get_by_role("textbox")
    try:
        count = boxes.count()
    except Exception:
        count = 0
    if count > 1:
        return boxes.nth(count - 1)
    return boxes


def _paste_shortcut() -> str:
    return "Meta+V" if platform.system().lower() == "darwin" else "Control+V"


def _read_chatbox_text(chatbox) -> str:
    try:
        return str(chatbox.inner_text(timeout=5_000) or "")
    except Exception:
        try:
            return str(chatbox.evaluate("el => el.innerText || el.textContent || el.value || ''") or "")
        except Exception:
            return ""


def _prompt_looks_placed(expected: str, actual: str) -> bool:
    actual = actual or ""
    if not actual.strip():
        return False
    if "msgctxt" in expected:
        expected_count = expected.count("msgctxt")
        actual_count = actual.count("msgctxt")
        if actual_count < min(3, expected_count):
            return False
        if "msgid" in expected and "msgid" not in actual:
            return False
        if "msgstr" in expected and "msgstr" not in actual:
            return False
    return True


def _clear_chatbox(page, chatbox) -> None:
    try:
        chatbox.click(timeout=10_000)
        page.keyboard.press("Control+A")
        page.keyboard.press("Backspace")
        page.wait_for_timeout(150)
        return
    except Exception:
        pass
    try:
        chatbox.evaluate(
            """el => {
                el.focus();
                el.innerHTML = '<p><br></p>';
                el.textContent = '';
                el.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'deleteContent'}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
            }"""
        )
    except Exception:
        pass


def _try_clipboard_paste(page, chatbox, text: str) -> str:
    try:
        try:
            page.context.grant_permissions(["clipboard-read", "clipboard-write"], origin="https://gemini.google.com")
        except Exception:
            pass
        page.evaluate("async (txt) => { await navigator.clipboard.writeText(txt); }", text)
        chatbox.click(timeout=10_000)
        page.keyboard.press(_paste_shortcut())
        page.wait_for_timeout(700)
        return _read_chatbox_text(chatbox)
    except Exception:
        return ""


def _try_dispatch_paste(chatbox, text: str) -> str:
    try:
        return str(chatbox.evaluate(
            """(el, txt) => {
                el.focus();
                try {
                    const dt = new DataTransfer();
                    dt.setData('text/plain', txt);
                    const evt = new ClipboardEvent('paste', {bubbles: true, cancelable: true, clipboardData: dt});
                    el.dispatchEvent(evt);
                } catch (e) {}
                el.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertFromPaste', data: txt}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
                return el.innerText || el.textContent || el.value || '';
            }""",
            text,
        ) or "")
    except Exception:
        return ""


def _try_exec_insert(page, chatbox, text: str) -> str:
    try:
        chatbox.click(timeout=10_000)
        placed = chatbox.evaluate(
            """(el, txt) => {
                el.focus();
                const selection = window.getSelection();
                const range = document.createRange();
                range.selectNodeContents(el);
                selection.removeAllRanges();
                selection.addRange(range);
                document.execCommand('delete', false, null);
                document.execCommand('insertText', false, txt);
                el.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: txt}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
                return el.innerText || el.textContent || el.value || '';
            }""",
            text,
        )
        page.wait_for_timeout(500)
        return _read_chatbox_text(chatbox) or str(placed or "")
    except Exception:
        return ""


def _try_safe_inner_html(page, chatbox, text: str) -> str:
    try:
        placed = chatbox.evaluate(
            """(el, txt) => {
                function esc(s) {
                    return s.replace(/&/g, '&amp;')
                            .replace(/</g, '&lt;')
                            .replace(/>/g, '&gt;')
                            .replace(/\"/g, '&quot;');
                }
                el.focus();
                const lines = txt.split('\n');
                el.innerHTML = lines.map(line => line ? `<p>${esc(line)}</p>` : '<p><br></p>').join('');
                el.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: txt}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
                return el.innerText || el.textContent || el.value || '';
            }""",
            text,
        )
        page.wait_for_timeout(500)
        return _read_chatbox_text(chatbox) or str(placed or "")
    except Exception:
        return ""


def _place_prompt_in_chatbox(page, text: str) -> str:
    chatbox = _active_chatbox(page)
    chatbox.click(timeout=20_000)

    attempts = (
        _try_clipboard_paste,
        _try_dispatch_paste,
        _try_exec_insert,
        _try_safe_inner_html,
    )

    last = ""
    for attempt in attempts:
        _clear_chatbox(page, chatbox)
        if attempt is _try_clipboard_paste:
            placed = attempt(page, chatbox, text)
        elif attempt is _try_exec_insert or attempt is _try_safe_inner_html:
            placed = attempt(page, chatbox, text)
        else:
            placed = attempt(chatbox, text)
        last = placed or ""
        if _prompt_looks_placed(text, last):
            return last

    return last

def _click_send_button(page) -> bool:
    """Click only a real enabled Gemini Send button.

    Avoid the stuck disabled Stop-response box. The older fallback clicked any
    button inside send-button-container, which could falsely report success when
    Gemini showed an unclickable disabled Stop button.
    """
    try:
        clicked = bool(page.evaluate(
            """() => {
                const isVisible = (el) => {
                    if (!el || el.hidden) return false;
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    return rect.width > 0
                        && rect.height > 0
                        && style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && style.opacity !== '0';
                };
                const isDisabled = (el) => {
                    if (!el) return true;
                    return el.disabled
                        || el.hasAttribute('disabled')
                        || el.getAttribute('aria-disabled') === 'true'
                        || !!el.closest('[aria-disabled="true"], .disabled');
                };
                const hasStopIcon = (root) => {
                    if (!root) return false;
                    const icon = root.querySelector('mat-icon[fonticon="stop"], mat-icon[data-mat-icon-name="stop"]');
                    return !!icon && isVisible(icon);
                };
                const hasSendIcon = (root) => !!root?.querySelector(
                    'mat-icon[fonticon="send"], mat-icon[data-mat-icon-name="send"], mat-icon[fonticon="arrow_upward"], mat-icon[data-mat-icon-name="arrow_upward"]'
                );
                const looksLikeSend = (btn) => {
                    const root = btn.closest('div[data-test-id="send-button-container"], gem-icon-button.send-button') || btn;
                    const label = (btn.getAttribute('aria-label') || root.getAttribute('aria-label') || '').toLowerCase();
                    const title = (btn.getAttribute('title') || root.getAttribute('title') || '').toLowerCase();
                    const classes = `${btn.className || ''} ${root.className || ''}`.toLowerCase();
                    if (label.includes('stop') || label.includes('dừng') || title.includes('stop') || classes.split(/\\s+/).includes('stop') || hasStopIcon(root)) return false;
                    return label.includes('send')
                        || label.includes('gửi')
                        || title.includes('send')
                        || hasSendIcon(root)
                        || root.matches('div[data-test-id="send-button-container"]');
                };
                const candidates = Array.from(document.querySelectorAll(
                    'div[data-test-id="send-button-container"] button, gem-icon-button.send-button button, button[aria-label="Send message"], button[aria-label*="Send"], button[aria-label*="Gửi"]'
                ));
                const btn = candidates.find((candidate) => {
                    const root = candidate.closest('div[data-test-id="send-button-container"], gem-icon-button.send-button') || candidate;
                    return isVisible(candidate) && isVisible(root) && !isDisabled(candidate) && !isDisabled(root) && looksLikeSend(candidate);
                });
                if (!btn) return false;
                btn.click();
                return true;
            }"""
        ))
        if clicked:
            return True
    except Exception:
        pass

    for selector in (
        'div[data-test-id="send-button-container"]:not(.disabled) button[aria-label="Send message"]',
        'gem-icon-button.send-button:not(.stop):not([aria-disabled="true"]) button[aria-label="Send message"]',
        'button[aria-label="Send message"]',
    ):
        try:
            button = page.locator(selector).last
            button.wait_for(state="visible", timeout=3_000)
            label = (button.get_attribute("aria-label", timeout=1_000) or "").lower()
            if "stop" in label or "dừng" in label:
                continue
            button.click(timeout=3_000, force=True)
            return True
        except Exception:
            pass

    return False


def send_to_gemini(page, text: str, max_wait_seconds: int = 180, stop_requested: StopFn | None = None) -> str:
    check_stop(stop_requested)
    try:
        page.bring_to_front()
    except Exception:
        pass
    if _refresh_page_for_unclickable_stop_box(page):
        raise RuntimeError("Gemini UI was stuck on a disabled Stop response box before send. Refreshed page; batch will retry.")
    placed = _place_prompt_in_chatbox(page, text)
    check_stop(stop_requested)
    if not _prompt_looks_placed(text, placed):
        sample = (placed or "").replace("\n", " ")[:180]
        raise RuntimeError(f"Gemini textbox did not receive the PO entries. Prompt placement failed before send. Got: {sample!r}")

    response_index = _count_responses(page)
    sleep_with_stop(0.8, stop_requested)

    clicked = _click_send_button(page)
    check_stop(stop_requested)
    if not clicked:
        if _refresh_page_for_unclickable_stop_box(page):
            raise RuntimeError("Gemini showed a disabled Stop response box instead of an enabled Send button. Refreshed page; batch will retry.")
        # Last fallback only. Current Gemini UI needs the Send button click.
        page.keyboard.press("Control+Enter")
        sleep_with_stop(0.5, stop_requested)
        if _count_responses(page) == response_index:
            page.keyboard.press("Enter")

    # Do not accidentally read the previous answer if the prompt was not submitted.
    deadline = time.time() + min(30, max(12, int(max_wait_seconds) // 4))
    while time.time() < deadline:
        check_stop(stop_requested)
        if _count_responses(page) > response_index:
            break
        sleep_with_stop(0.5, stop_requested)
    if _count_responses(page) <= response_index:
        if _refresh_page_for_unclickable_stop_box(page):
            raise RuntimeError("Gemini got stuck on a disabled Stop response box after submit. Refreshed page; batch will retry.")
        _click_stop_button(page)
        raise RuntimeError("Gemini prompt was placed, but no new response appeared. The Send button may not have submitted or the tab was interrupted.")

    return _wait_for_generation_to_finish(page, response_index, max_wait_seconds=max_wait_seconds, stop_requested=stop_requested)


def _skip_path(path: Path) -> bool:
    return any("SKIP" in part for part in path.parts)


def discover_untranslated_po_files(root: str | Path, max_files: int | None = None) -> list[Path]:
    base = Path(root)
    po_paths = [base] if base.is_file() else list(iter_po_files(base))
    found: list[Path] = []
    for po_path in po_paths:
        if _skip_path(po_path):
            continue
        try:
            po = load_po(po_path)
        except Exception:
            continue
        if any(not entry.msgstr.strip() for entry in po.entries):
            found.append(po_path)
            if max_files is not None and len(found) >= max_files:
                break
    return found


def _safe_folder_label(raw: str) -> str:
    label = raw.strip().strip('"').strip("'")
    label = re.sub(r"[\\/:*?\"<>|]", "", label)
    label = re.sub(r"[^A-Za-z0-9 ]+", "", label)
    label = re.sub(r"\s+", " ", label).strip()
    words = label.split()
    if len(words) > 4:
        label = " ".join(words[:4])
    return label[:48].strip()


def _rename_folder_if_needed(folder: Path, segment_id: str, label: str) -> tuple[Path | None, str]:
    if not label:
        return None, "empty label"
    if not all(ord(c) < 128 for c in label):
        return None, "label is not ASCII"
    if len(label) >= 50:
        return None, "label too long"
    # Already renamed: folder has text after the segment id.
    if folder.name != segment_id and folder.name.startswith(segment_id + " "):
        return None, "folder already has a label"

    new_folder = folder.with_name(f"{folder.name} {label}")
    if new_folder == folder:
        return None, "same folder name"
    if new_folder.exists():
        return None, "target folder already exists"
    folder.rename(new_folder)
    return new_folder, ""


def _folder_already_has_label(folder: Path, segment_id: str) -> bool:
    """Return True when a segment folder already has a human label.

    Example: folder "001 Classroom Intro" with segment_id "001".
    In that case we skip the Gemini summary request entirely.
    """
    return folder.name != segment_id and folder.name.startswith(segment_id + " ")


def translate_po_file_via_web(
    session: GeminiWebSession,
    po_path: str | Path,
    max_lines_per_batch: int = 600,
    wait_between_batches: float = 8.0,
    allow_invalid: AllowInvalid = False,
    rename_folder: bool = True,
    response_timeout_seconds: int = 180,
    max_entries_per_batch: int = DEFAULT_MAX_ENTRIES_PER_BATCH,
    retry_count: int = DEFAULT_BATCH_RETRIES,
    log: LogFn | None = None,
    stop_requested: StopFn | None = None,
) -> WebTranslateFileResult:
    def say(message: str) -> None:
        if log:
            log(message)

    check_stop(stop_requested)
    po_path = Path(po_path)
    po = load_po(po_path)
    total_entries = len(po.entries)
    missing_before = sum(1 for entry in po.entries if not entry.msgstr.strip())
    result = WebTranslateFileResult(file=po_path, missing_before=missing_before, total_entries=total_entries, translated=0)

    if missing_before <= 0:
        return result

    # Use the working PO file as the source for Gemini Web.
    # Copy.po is backup/reference only and must never drive translation input,
    # because stale or mismatched Copy.po files can share the same msgctxt IDs.
    source_entries = [entry for entry in po.entries if not entry.msgstr.strip()]
    debug_path = po_path.with_name(f"{po_path.stem}_translated.txt")
    result.debug_log = debug_path
    debug_path.write_text(f"--- Gemini Translation Raw Output for {po_path.name} ---\n\n", encoding="utf-8")

    batches = batch_entries_by_lines(
        source_entries,
        max_lines_per_batch=max_lines_per_batch,
        max_entries_per_batch=max_entries_per_batch,
    )
    all_valid_translations: dict[str, str] = {}
    all_invalid_translations: dict[str, str] = {}

    say(f"File: {po_path.name} | missing {missing_before}/{total_entries}")
    for idx, batch in enumerate(batches, start=1):
        check_stop(stop_requested)
        say(f"Batch {idx}/{len(batches)} | {len(batch)} entries")
        entries_text = "\n".join(entry_to_po_prompt_block(entry) for entry in batch)
        prompt = TRANSLATE_PROMPT_TEMPLATE.format(entries=entries_text)
        response = ""
        parsed_by_ctx: dict[str, str] = {}
        translations_by_uid: dict[str, str] = {}
        last_exc: Exception | None = None
        normal_attempts = max(1, int(retry_count) + 1)
        # After all normal retries fail, force-refresh Gemini and try the same
        # batch one final time. This recovers from hidden/stale Gemini UI states
        # that are not fixed by Escape/Stop/clear-composer recovery.
        total_attempts = normal_attempts + 1

        with debug_path.open("a", encoding="utf-8") as f:
            f.write(f"==================== BATCH {idx} REQUEST ====================\n")
            f.write(entries_text)
            f.write("\n")

        for attempt in range(1, total_attempts + 1):
            check_stop(stop_requested)
            if attempt > normal_attempts:
                say(f"  Retries failed for batch {idx}/{len(batches)}. Refreshing Gemini page and trying once more")
                session.refresh_page()
                sleep_with_stop(3, stop_requested)
            elif attempt > 1:
                say(f"  Retry batch {idx}/{len(batches)} attempt {attempt}/{normal_attempts}")
                session.recover_after_error()
                sleep_with_stop(2, stop_requested)
            try:
                response = session.send(prompt, max_wait_seconds=response_timeout_seconds, stop_requested=stop_requested)
                parsed_by_ctx = parse_translated_po_response(response)
                batch_by_ctx = {entry.msgctxt or "": entry for entry in batch}
                translations_by_uid = {}
                for ctx, translation in parsed_by_ctx.items():
                    entry = batch_by_ctx.get(ctx)
                    if entry is not None:
                        translations_by_uid[entry.uid] = translation
                if translations_by_uid:
                    break
                last_exc = RuntimeError("Gemini response had no parseable msgctxt/msgstr entries")
                with debug_path.open("a", encoding="utf-8") as f:
                    f.write(f"==================== BATCH {idx} ATTEMPT {attempt} EMPTY/PARSE FAIL ====================\n")
                    f.write(response)
                    f.write("\n\n")
                session.recover_after_error()
            except Exception as exc:
                last_exc = exc
                with debug_path.open("a", encoding="utf-8") as f:
                    f.write(f"==================== BATCH {idx} ATTEMPT {attempt} ERROR ====================\n")
                    f.write(str(exc))
                    f.write("\n\n")
                session.recover_after_error()
                if attempt >= total_attempts:
                    session.refresh_page()
                    raise RuntimeError(
                        f"Batch {idx}/{len(batches)} failed after {normal_attempts} normal attempt(s) "
                        f"plus 1 refresh attempt: {exc}"
                    ) from exc

        if not translations_by_uid and last_exc is not None:
            session.refresh_page()
            raise RuntimeError(
                f"Batch {idx}/{len(batches)} failed after {normal_attempts} normal attempt(s) "
                f"plus 1 refresh attempt: {last_exc}"
            )

        with debug_path.open("a", encoding="utf-8") as f:
            f.write(f"==================== BATCH {idx} RESPONSE ====================\n")
            f.write(response)
            f.write("\n\n")


        errors = validate_translations(batch, translations_by_uid)
        invalid_uids = {err.uid for err in errors if err.reason != "missing translation"}
        allow_invalid_now = _allow_invalid_enabled(allow_invalid)
        accepted = 0
        for uid, translation in translations_by_uid.items():
            if uid in invalid_uids:
                all_invalid_translations[uid] = translation
                if allow_invalid_now:
                    accepted += 1
            else:
                all_valid_translations[uid] = translation
                accepted += 1

        if invalid_uids:
            say(f"  Allow invalid now: {'ON' if allow_invalid_now else 'OFF'}")

        batch_result = WebBatchResult(
            batch_index=idx,
            batch_total=len(batches),
            requested=len(batch),
            parsed=len(translations_by_uid),
            accepted=accepted,
            errors=errors,
        )
        result.batches.append(batch_result)
        result.errors.extend(errors)

        if accepted == len(batch):
            say(f"  OK {accepted}/{len(batch)}")
        else:
            say(f"  Parsed {len(translations_by_uid)}/{len(batch)}, accepted {accepted}, errors {len(errors)}")

        if idx < len(batches) and wait_between_batches:
            sleep_with_stop(float(wait_between_batches), stop_requested)

    check_stop(stop_requested)
    allow_invalid_at_save = _allow_invalid_enabled(allow_invalid)
    translations_to_save = dict(all_valid_translations)
    if allow_invalid_at_save:
        translations_to_save.update(all_invalid_translations)

    if all_invalid_translations:
        say(
            f"Allow invalid at save: {'ON' if allow_invalid_at_save else 'OFF'} | "
            f"invalid translations {'included' if allow_invalid_at_save else 'skipped'}: {len(all_invalid_translations)}"
        )

    if translations_to_save:
        fresh_po = load_po(po_path)
        changed = patch_msgstr_by_uid(fresh_po, translations_to_save)
        if changed:
            save_po(fresh_po, po_path)
        result.translated = changed
        say(f"Saved {changed} translations to {po_path.name}")
    else:
        say("No translations accepted; PO file not changed")

    if rename_folder and result.translated > 0:
        folder = po_path.parent
        # Prefer the working PO stem as segment id. Fall back to the first folder token
        # for older layouts where the PO/folder names may differ.
        stem_segment_id = po_path.stem.strip()
        folder_segment_id = folder.name.split()[0] if " " in folder.name else folder.name
        segment_id = stem_segment_id if folder.name == stem_segment_id or folder.name.startswith(stem_segment_id + " ") else folder_segment_id

        if _folder_already_has_label(folder, segment_id):
            result.folder_rename_skipped_reason = "folder already has a label"
            say("Folder rename skipped: folder already has a label")
        else:
            context_entries = source_entries[:6]
            samples = "\n".join(f"- {entry.msgid}" for entry in context_entries)
            summary_prompt = SUMMARY_PROMPT.format(samples=samples)
            say("Requesting folder summary")
            check_stop(stop_requested)
            try:
                summary_raw = session.send(summary_prompt, max_wait_seconds=response_timeout_seconds, stop_requested=stop_requested)
            except Exception as exc:
                session.recover_after_error()
                result.folder_rename_skipped_reason = f"summary request failed: {exc}"
                say(f"Folder rename skipped: summary request failed ({exc})")
                return result
            with debug_path.open("a", encoding="utf-8") as f:
                f.write("==================== FOLDER SUMMARY ====================\n")
                f.write(summary_raw)
                f.write("\n\n")
            label = _safe_folder_label(summary_raw)
            new_folder, reason = _rename_folder_if_needed(folder, segment_id, label)
            if new_folder:
                result.folder_renamed_from = folder
                result.folder_renamed_to = new_folder
                say(f"Renamed folder: {folder.name} -> {new_folder.name}")
            else:
                result.folder_rename_skipped_reason = reason
                say(f"Folder rename skipped: {reason}")

    return result


def run_gemini_web_path(
    path: str | Path,
    max_files: int | None = 59,
    max_lines_per_batch: int = 600,
    wait_between_batches: float = 8.0,
    cdp_url: str = DEFAULT_CDP_URL,
    allow_invalid: AllowInvalid = False,
    rename_duplicates: bool = True,
    create_missing_backups: bool = True,
    rename_folders: bool = True,
    response_timeout_seconds: int = 180,
    max_entries_per_batch: int = DEFAULT_MAX_ENTRIES_PER_BATCH,
    retry_count: int = DEFAULT_BATCH_RETRIES,
    log: LogFn | None = None,
    stop_requested: StopFn | None = None,
) -> WebTranslateRunResult:
    check_stop(stop_requested)
    base = Path(path)
    renamed_duplicates: list[RenameChange] = []

    def say(message: str) -> None:
        if log:
            log(message)

    if base.is_dir() and rename_duplicates:
        renamed_duplicates = normalize_duplicate_names(base)
        for change in renamed_duplicates:
            if change.skipped:
                say(f"Rename skip: {change.old} -> {change.new} ({change.reason})")
            else:
                say(f"Renamed: {change.old} -> {change.new}")

    check_stop(stop_requested)
    po_files = discover_untranslated_po_files(base, max_files=max_files)
    if not po_files:
        return WebTranslateRunResult(renamed_duplicates=renamed_duplicates, files=[])

    results: list[WebTranslateFileResult] = []
    with GeminiWebSession(cdp_url=cdp_url) as session:
        for po_path in po_files:
            check_stop(stop_requested)
            backup_created = False
            if create_missing_backups and not find_backup_for_file(po_path):
                # Safe backup creation only. Existing Copy.po files are never overwritten.
                backup_path = po_path.with_name(f"{po_path.stem} - Copy.po")
                if backup_path.exists():
                    say(f"Copy.po exists, not touched: {backup_path.name}")
                else:
                    check_stop(stop_requested)
                    shutil.copy2(po_path, backup_path)
                    backup_created = True
                    say(f"Created missing backup: {backup_path.name}")

            file_result = translate_po_file_via_web(
                session,
                po_path,
                max_lines_per_batch=max_lines_per_batch,
                wait_between_batches=wait_between_batches,
                allow_invalid=allow_invalid,
                rename_folder=rename_folders,
                response_timeout_seconds=response_timeout_seconds,
                max_entries_per_batch=max_entries_per_batch,
                retry_count=retry_count,
                log=log,
                stop_requested=stop_requested,
            )
            file_result.backup_created = backup_created
            results.append(file_result)

    return WebTranslateRunResult(renamed_duplicates=renamed_duplicates, files=results)
