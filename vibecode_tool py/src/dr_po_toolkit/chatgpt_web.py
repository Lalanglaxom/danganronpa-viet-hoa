from __future__ import annotations

import re
import shutil
import platform
import time
from pathlib import Path

from .cancel import OperationCancelled, StopFn, check_stop, sleep_with_stop
from .cleanup import RenameChange, normalize_duplicate_names
from .discovery import find_backup_for_file
from .gemini_web import (
    AllowInvalid,
    DEFAULT_BATCH_RETRIES,
    DEFAULT_CDP_URL,
    DEFAULT_MAX_ENTRIES_PER_BATCH,
    MIN_POST_SAVE_DELAY_SECONDS,
    LogFn,
    ProgressFn,
    PromptAlreadySentError,
    WebTranslateFileResult,
    WebTranslateRunResult,
    discover_untranslated_po_files,
    translate_po_file_via_web,
)

DEFAULT_CHATGPT_URL = "https://chatgpt.com/"
PROMPT_EDITOR_SEL = (
    '#prompt-textarea[contenteditable="true"][role="textbox"], '
    'div.ProseMirror[contenteditable="true"][role="textbox"]'
)
DEFAULT_NO_PROGRESS_TIMEOUT_SECONDS = 60

CHATGPT_TRANSLATE_PROMPT_TEMPLATE = """Translate ONLY the .po entries included below for the Danganronpa project into Vietnamese.
Use my ChatGPT memory and custom instructions for character-specific tones and terminology if available.
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


def _is_chatgpt_url(url: str) -> bool:
    lowered = (url or "").lower()
    return "chatgpt.com" in lowered or "chat.openai.com" in lowered


def _normalize_prompt_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def _prompt_looks_placed(expected: str, actual: str) -> bool:
    normalized_expected = _normalize_prompt_text(expected)
    normalized_actual = _normalize_prompt_text(actual)
    if not normalized_expected or not normalized_actual:
        return False
    if normalized_actual == normalized_expected:
        return True
    prefix = normalized_expected[: min(160, len(normalized_expected))]
    if not normalized_actual.startswith(prefix):
        return False
    if "msgctxt" in expected:
        return actual.count("msgctxt") >= min(3, expected.count("msgctxt"))
    return True


class ChatGPTWebSession:
    """Control a signed-in ChatGPT tab through Chrome CDP without navigation.

    A prompt is never resent after ChatGPT accepts it. Recovery may clear a
    still-unsent composer, but this session deliberately does not reload or
    navigate the user's active ChatGPT tab.
    """

    provider_name = "ChatGPT"
    allow_page_refresh_retry = False
    allow_retry_after_response = False

    def __init__(self, cdp_url: str = DEFAULT_CDP_URL):
        self.cdp_url = cdp_url
        self._playwright = None
        self._browser = None
        self.page = None
        self._last_entries_saved_at: float | None = None

    def note_entries_saved(self) -> None:
        self._last_entries_saved_at = time.monotonic()

    def _wait_after_saved_entries(self, stop_requested: StopFn | None = None) -> None:
        if self._last_entries_saved_at is None:
            return
        elapsed = time.monotonic() - self._last_entries_saved_at
        remaining = MIN_POST_SAVE_DELAY_SECONDS - elapsed
        if remaining > 0:
            sleep_with_stop(remaining, stop_requested)
        self._last_entries_saved_at = None

    def __enter__(self) -> "ChatGPTWebSession":
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

        try:
            self.page = self._pick_chatgpt_page()
        except Exception:
            self._playwright.stop()
            raise
        try:
            self.page.bring_to_front()
        except Exception:
            pass
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._playwright is not None:
            self._playwright.stop()

    def _pick_chatgpt_page(self):
        if self._browser is None:
            raise RuntimeError("ChatGPTWebSession is not connected")

        candidates = []
        for context in self._browser.contexts:
            for page in context.pages:
                try:
                    if page.is_closed() or not _is_chatgpt_url(page.url or ""):
                        continue
                    candidates.append(page)
                    editor = page.locator(PROMPT_EDITOR_SEL).first
                    if editor.count() > 0 and editor.is_visible(timeout=300):
                        return page
                except Exception:
                    continue

        if candidates:
            return candidates[-1]
        raise RuntimeError(
            "No ChatGPT tab found. Click Open Chrome, sign in to ChatGPT, and keep the chat box visible. "
            "The toolkit did not navigate or refresh any page."
        )

    def _ensure_page(self):
        if self._browser is None:
            raise RuntimeError("ChatGPTWebSession is not connected")
        try:
            if self.page is not None and not self.page.is_closed() and _is_chatgpt_url(self.page.url or ""):
                self.page.bring_to_front()
                return self.page
        except Exception:
            pass

        self.page = self._pick_chatgpt_page()
        try:
            self.page.bring_to_front()
        except Exception:
            pass
        return self.page

    def recover_after_error(self) -> None:
        """Recover only while a prompt is still unsent; never reload the page."""
        try:
            page = self._ensure_page()
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
            if not self._is_generation_running(page) and self._prompt_still_present(page):
                self._clear_editor(page)
        except Exception:
            pass

    def refresh_page(self) -> None:
        """ChatGPT recovery intentionally avoids page reloads and navigation."""
        self.recover_after_error()

    def send(self, text: str, max_wait_seconds: int = 180, stop_requested: StopFn | None = None) -> str:
        self._wait_after_saved_entries(stop_requested)
        page = self._ensure_page()
        self._wait_until_ready(page, stop_requested=stop_requested)
        self._raise_if_rate_limited(page)

        before_assistant_turns = self._assistant_turns(page)
        before_user_count = self._user_turn_count(page)
        self._set_prompt(page, text, stop_requested=stop_requested)
        accepted = self._send_prompt(page, before_user_count=before_user_count, stop_requested=stop_requested)
        if not accepted:
            raise RuntimeError("ChatGPT did not accept the prompt. The composer was left unsent for a safe retry.")

        try:
            return self._wait_for_response(
                page,
                before_assistant_turns=before_assistant_turns,
                max_wait_seconds=max_wait_seconds,
                stop_requested=stop_requested,
            )
        except OperationCancelled:
            raise
        except Exception as exc:
            raise PromptAlreadySentError(
                "Prompt was already sent to ChatGPT and was not resent. "
                f"Response monitoring failed: {exc}"
            ) from exc

    @staticmethod
    def _read_editor_text(page) -> str:
        try:
            return str(
                page.evaluate(
                    """
                    (selector) => {
                        const el = document.querySelector(selector);
                        return el ? (el.innerText || el.textContent || '') : '';
                    }
                    """,
                    PROMPT_EDITOR_SEL,
                )
                or ""
            )
        except Exception:
            return ""

    @staticmethod
    def _clear_editor(page) -> None:
        try:
            editor = page.locator(PROMPT_EDITOR_SEL).first
            editor.click(timeout=5_000)
            select_all = "Meta+A" if platform.system().lower() == "darwin" else "Control+A"
            page.keyboard.press(select_all)
            page.keyboard.press("Backspace")
            page.wait_for_timeout(150)
            if not ChatGPTWebSession._read_editor_text(page).strip():
                return
        except Exception:
            pass

        page.evaluate(
            """
            (selector) => {
                const el = document.querySelector(selector);
                if (!el) throw new Error('Editor not found');
                el.focus();
                const selection = window.getSelection();
                const range = document.createRange();
                range.selectNodeContents(el);
                selection.removeAllRanges();
                selection.addRange(range);
                try { document.execCommand('delete', false, null); } catch (_) {}
                el.innerHTML = '<p><br class="ProseMirror-trailingBreak"></p>';
                el.dispatchEvent(new InputEvent('input', {
                    bubbles: true,
                    inputType: 'deleteContentBackward',
                    data: null
                }));
            }
            """,
            PROMPT_EDITOR_SEL,
        )

    @staticmethod
    def _set_prompt_with_dom_paste(page, prompt: str) -> None:
        page.evaluate(
            """
            ({ selector, text }) => {
                const el = document.querySelector(selector);
                if (!el) throw new Error('Editor not found');
                el.focus();

                const selection = window.getSelection();
                const range = document.createRange();
                range.selectNodeContents(el);
                selection.removeAllRanges();
                selection.addRange(range);
                try { document.execCommand('delete', false, null); } catch (_) {}

                let handled = false;
                try {
                    const data = new DataTransfer();
                    data.setData('text/plain', text);
                    const event = new ClipboardEvent('paste', {
                        bubbles: true,
                        cancelable: true,
                        clipboardData: data,
                    });
                    handled = !el.dispatchEvent(event);
                } catch (_) {}

                if (!handled && (el.textContent || '').trim().length === 0) {
                    try { document.execCommand('insertText', false, text); } catch (_) {}
                }

                if ((el.textContent || '').trim().length === 0) {
                    el.innerHTML = '';
                    for (const line of String(text).split('\\n')) {
                        const p = document.createElement('p');
                        if (line.length) p.textContent = line;
                        else p.appendChild(document.createElement('br'));
                        el.appendChild(p);
                    }
                }

                el.dispatchEvent(new InputEvent('input', {
                    bubbles: true,
                    inputType: 'insertText',
                    data: text
                }));
            }
            """,
            {"selector": PROMPT_EDITOR_SEL, "text": prompt},
        )

    def _set_prompt(self, page, prompt: str, stop_requested: StopFn | None = None) -> None:
        check_stop(stop_requested)
        editor = page.locator(PROMPT_EDITOR_SEL).first
        editor.wait_for(state="visible", timeout=15_000)
        methods = (
            ("fill", lambda: editor.fill(prompt, timeout=20_000)),
            ("keyboard", lambda: page.keyboard.insert_text(prompt)),
            ("dom paste", lambda: self._set_prompt_with_dom_paste(page, prompt)),
        )
        errors: list[str] = []

        for name, method in methods:
            check_stop(stop_requested)
            try:
                self._clear_editor(page)
                editor.click(timeout=5_000)
                method()
                page.wait_for_timeout(700)
                actual = self._read_editor_text(page)
                if _prompt_looks_placed(prompt, actual):
                    page.evaluate(
                        """
                        (selector) => {
                            const el = document.querySelector(selector);
                            if (!el) return;
                            el.focus();
                            el.dispatchEvent(new InputEvent('input', {
                                bubbles: true,
                                inputType: 'insertText',
                                data: el.textContent || ''
                            }));
                        }
                        """,
                        PROMPT_EDITOR_SEL,
                    )
                    page.wait_for_timeout(350)
                    return
                errors.append(f"{name}: prompt verification failed")
            except Exception as exc:
                errors.append(f"{name}: {str(exc)[:120]}")

        raise RuntimeError("Prompt did not enter the ChatGPT text box. " + "; ".join(errors[-3:]))

    def _wait_until_ready(self, page, stop_requested: StopFn | None = None) -> None:
        deadline = time.time() + 90.0
        while time.time() < deadline:
            check_stop(stop_requested)
            self._raise_if_rate_limited(page)
            try:
                editor = page.locator(PROMPT_EDITOR_SEL).first
                visible = editor.count() > 0 and editor.is_visible(timeout=300)
            except Exception:
                visible = False
            if visible and not self._is_generation_running(page):
                return
            sleep_with_stop(0.25, stop_requested)
        raise RuntimeError(
            "ChatGPT is not ready. Make sure debug Chrome is signed in, the chat box is visible, "
            "and no earlier response is still generating."
        )

    @staticmethod
    def _prompt_still_present(page) -> bool:
        return bool(ChatGPTWebSession._read_editor_text(page).strip())

    @staticmethod
    def _find_send_button_and_click(page) -> bool:
        try:
            return bool(
                page.evaluate(
                    """
                    () => {
                        const isVisible = (el) => {
                            if (!el) return false;
                            const rect = el.getBoundingClientRect();
                            const style = window.getComputedStyle(el);
                            return rect.width > 0 && rect.height > 0
                                && style.display !== 'none'
                                && style.visibility !== 'hidden'
                                && style.opacity !== '0';
                        };
                        const isDisabled = (el) => el.disabled
                            || el.hasAttribute('disabled')
                            || el.getAttribute('aria-disabled') === 'true';
                        const looksLikeSend = (btn) => {
                            const id = (btn.id || '').toLowerCase();
                            const testId = (btn.getAttribute('data-testid') || '').toLowerCase();
                            const label = (btn.getAttribute('aria-label') || '').toLowerCase();
                            const title = (btn.getAttribute('title') || '').toLowerCase();
                            const text = (btn.textContent || '').trim().toLowerCase();
                            const all = `${id} ${testId} ${label} ${title} ${text}`;
                            if (all.includes('voice') || all.includes('dictation') || all.includes('stop')) return false;
                            return id === 'composer-submit-button'
                                || testId.includes('send')
                                || label.includes('send')
                                || label.includes('submit')
                                || title.includes('send')
                                || text === 'send';
                        };
                        const candidates = Array.from(document.querySelectorAll(
                            '#composer-submit-button, button[data-testid*="send"], button[aria-label*="Send"], button[aria-label*="Submit"], form[data-type="unified-composer"] button, [data-composer-surface="true"] button'
                        ));
                        const btn = candidates.find((candidate) => isVisible(candidate) && !isDisabled(candidate) && looksLikeSend(candidate));
                        if (!btn) return false;
                        btn.scrollIntoView({block: 'center', inline: 'center'});
                        btn.click();
                        return true;
                    }
                    """
                )
            )
        except Exception:
            return False

    @staticmethod
    def _user_turn_count(page) -> int:
        try:
            return int(
                page.evaluate(
                    """
                    () => {
                        const nodes = new Set();
                        for (const selector of [
                            '[data-message-author-role="user"]',
                            'section[data-turn="user"]',
                            '[data-testid^="conversation-turn"] [data-message-author-role="user"]'
                        ]) {
                            document.querySelectorAll(selector).forEach((node) => {
                                const turn = node.closest('section[data-turn-id], section[data-turn], [data-testid^="conversation-turn"], article') || node;
                                nodes.add(turn);
                            });
                        }
                        return nodes.size;
                    }
                    """
                )
                or 0
            )
        except Exception:
            return 0

    @staticmethod
    def _is_generation_running(page) -> bool:
        try:
            return bool(
                page.evaluate(
                    """
                    () => {
                        const visible = (el) => {
                            if (!el) return false;
                            const r = el.getBoundingClientRect();
                            const style = window.getComputedStyle(el);
                            return r.width > 0 && r.height > 0
                                && style.display !== 'none'
                                && style.visibility !== 'hidden'
                                && style.opacity !== '0';
                        };
                        const streamingNodes = Array.from(document.querySelectorAll(
                            '[data-is-streaming="true"], .result-streaming, [class*="result-streaming"], main [aria-busy="true"]'
                        ));
                        if (streamingNodes.some(visible)) return true;
                        return Array.from(document.querySelectorAll('button')).some((btn) => {
                            if (!visible(btn)) return false;
                            const testId = (btn.getAttribute('data-testid') || '').toLowerCase();
                            const label = (btn.getAttribute('aria-label') || '').toLowerCase();
                            const title = (btn.getAttribute('title') || '').toLowerCase();
                            const text = (btn.textContent || '').trim().toLowerCase();
                            const all = `${testId} ${label} ${title} ${text}`;
                            return all.includes('stop') && !all.includes('desktop notification');
                        });
                    }
                    """
                )
            )
        except Exception:
            return False

    @staticmethod
    def _click_stop(page) -> bool:
        try:
            return bool(
                page.evaluate(
                    """
                    () => {
                        const visible = (el) => {
                            if (!el) return false;
                            const r = el.getBoundingClientRect();
                            const style = window.getComputedStyle(el);
                            return r.width > 0 && r.height > 0
                                && style.display !== 'none'
                                && style.visibility !== 'hidden';
                        };
                        const btn = Array.from(document.querySelectorAll('button')).find((candidate) => {
                            if (!visible(candidate)) return false;
                            const all = `${candidate.getAttribute('data-testid') || ''} ${candidate.getAttribute('aria-label') || ''} ${candidate.getAttribute('title') || ''} ${candidate.textContent || ''}`.toLowerCase();
                            return all.includes('stop') && !candidate.disabled && candidate.getAttribute('aria-disabled') !== 'true';
                        });
                        if (!btn) return false;
                        btn.click();
                        return true;
                    }
                    """
                )
            )
        except Exception:
            return False

    def _wait_for_send_accepted(
        self,
        page,
        *,
        before_user_count: int,
        stop_requested: StopFn | None = None,
        timeout_seconds: float = 25.0,
    ) -> bool:
        deadline = time.time() + max(1.0, timeout_seconds)
        while time.time() < deadline:
            check_stop(stop_requested)
            self._raise_if_rate_limited(page)
            if self._user_turn_count(page) > before_user_count:
                return True
            if not self._prompt_still_present(page):
                return True
            if self._is_generation_running(page):
                return True
            sleep_with_stop(0.25, stop_requested)
        return False

    def _send_prompt(self, page, *, before_user_count: int, stop_requested: StopFn | None = None) -> bool:
        last_error: Exception | None = None
        for attempt in range(3):
            check_stop(stop_requested)
            self._raise_if_rate_limited(page)
            if self._user_turn_count(page) > before_user_count or not self._prompt_still_present(page) or self._is_generation_running(page):
                return True

            try:
                clicked = self._find_send_button_and_click(page)
                if not clicked:
                    editor = page.locator(PROMPT_EDITOR_SEL).first
                    editor.click(timeout=5_000)
                    page.keyboard.press("Enter")
                if self._wait_for_send_accepted(
                    page,
                    before_user_count=before_user_count,
                    stop_requested=stop_requested,
                ):
                    return True
                last_error = RuntimeError("Could not confirm ChatGPT accepted the send action")
            except OperationCancelled:
                raise
            except Exception as exc:
                last_error = exc
                if self._user_turn_count(page) > before_user_count or not self._prompt_still_present(page) or self._is_generation_running(page):
                    return True

            sleep_with_stop(1.5 * (attempt + 1), stop_requested)

        if last_error is not None:
            raise RuntimeError(f"Failed to send ChatGPT prompt safely: {last_error}") from last_error
        return False

    @staticmethod
    def _assistant_turns(page) -> list[dict[str, str]]:
        """Return visible assistant turns with stable-enough keys and clean text.

        ChatGPT can recycle or virtualize conversation DOM nodes, so callers
        must not assume that the assistant turn count always increases after a
        new prompt. Keys and text snapshots let monitoring detect an in-place
        replacement as well as an appended turn.
        """
        try:
            values = page.evaluate(
                """
                () => {
                    const turnSelector = [
                        'section[data-turn-id]',
                        'section[data-turn]',
                        '[data-testid^="conversation-turn"]',
                        '[data-testid*="conversation-turn"]',
                        '[data-message-id]',
                        'article'
                    ].join(', ');
                    const roots = [];
                    const seen = new Set();
                    const roleOf = (node) => {
                        if (!node) return '';
                        const own = (node.getAttribute('data-turn')
                            || node.getAttribute('data-message-author-role')
                            || '').toLowerCase();
                        if (own === 'assistant' || own === 'user') return own;
                        if (node.querySelector('[data-message-author-role="assistant"]')) return 'assistant';
                        if (node.querySelector('[data-message-author-role="user"]')) return 'user';
                        return '';
                    };
                    const add = (node) => {
                        if (!node) return;
                        const root = node.closest(turnSelector) || node;
                        if (roleOf(root) !== 'assistant' || seen.has(root)) return;
                        seen.add(root);
                        roots.push(root);
                    };

                    for (const selector of [
                        '[data-message-author-role="assistant"]',
                        '[data-turn="assistant"]',
                        'section[data-turn="assistant"]',
                        '[data-testid^="conversation-turn"] [data-message-author-role="assistant"]',
                        '[data-testid*="conversation-turn"] [data-message-author-role="assistant"]'
                    ]) {
                        document.querySelectorAll(selector).forEach(add);
                    }
                    document.querySelectorAll(turnSelector).forEach((node) => {
                        if (roleOf(node) === 'assistant') add(node);
                    });

                    return roots.map((root, index) => {
                        const message = root.matches('[data-message-author-role="assistant"]')
                            ? root
                            : (root.querySelector('[data-message-author-role="assistant"]') || root);
                        let codeNodes = Array.from(message.querySelectorAll(
                            'pre code, code[data-testid="code-content"], pre [class*="code"]'
                        ));
                        if (!codeNodes.length) codeNodes = Array.from(message.querySelectorAll('pre'));
                        const codeTexts = [];
                        const seenText = new Set();
                        for (const el of codeNodes) {
                            const value = (el.innerText || el.textContent || '').trim();
                            if (value && !seenText.has(value)) {
                                seenText.add(value);
                                codeTexts.push(value);
                            }
                        }
                        let text = codeTexts.join('\\n\\n');
                        if (!text) {
                            const body = message.querySelector(
                                '[class*="markdown"], .markdown, [data-testid*="message-content"]'
                            ) || message;
                            text = (body.innerText || body.textContent || '').trim();
                        }
                        const keyed = root.closest('[data-turn-id], [data-message-id], [data-testid]') || root;
                        const key = keyed.getAttribute('data-turn-id')
                            || keyed.getAttribute('data-message-id')
                            || keyed.getAttribute('data-testid')
                            || root.id
                            || `assistant:${index}`;
                        return { key: String(key), text: String(text || '') };
                    });
                }
                """
            )
            if isinstance(values, list):
                turns: list[dict[str, str]] = []
                for index, value in enumerate(values):
                    if not isinstance(value, dict):
                        continue
                    turns.append(
                        {
                            "key": str(value.get("key") or f"assistant:{index}"),
                            "text": str(value.get("text") or "").strip(),
                        }
                    )
                return turns
        except Exception:
            pass
        return []

    @classmethod
    def _assistant_responses(cls, page) -> list[str]:
        return [turn["text"] for turn in cls._assistant_turns(page)]

    @classmethod
    def _assistant_response_count(cls, page) -> int:
        return len(cls._assistant_turns(page))

    @staticmethod
    def _select_new_assistant_text(
        before_turns: list[dict[str, str]],
        current_turns: list[dict[str, str]],
    ) -> str:
        """Select output created after the baseline, even if node count is unchanged."""
        before_by_key = {
            str(turn.get("key") or ""): str(turn.get("text") or "").strip()
            for turn in before_turns
        }
        for turn in reversed(current_turns):
            key = str(turn.get("key") or "")
            current = str(turn.get("text") or "").strip()
            if not current:
                continue
            if key not in before_by_key or current != before_by_key.get(key, ""):
                return current

        if len(current_turns) > len(before_turns):
            return str(current_turns[-1].get("text") or "").strip()
        return ""

    @classmethod
    def _latest_new_response(cls, page, before_assistant_turns: list[dict[str, str]]) -> str:
        return cls._select_new_assistant_text(before_assistant_turns, cls._assistant_turns(page))

    @staticmethod
    def _rate_limit_popup_status(page) -> dict[str, object]:
        """Detect and dismiss visible ChatGPT rate-limit UI without retrying.

        Retry-style buttons are deliberately ignored because clicking them can
        submit another request. Only close/dismiss controls are used, with
        Escape as a safe fallback for modal overlays.
        """
        try:
            value = page.evaluate(
                """
                () => {
                    const phrases = [
                        'too many requests',
                        'rate limit',
                        "you've reached the current usage cap",
                        'you have reached the current usage cap',
                        'try again later'
                    ];
                    const visible = (el) => {
                        if (!el) return false;
                        const r = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);
                        return r.width > 0 && r.height > 0
                            && style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && style.opacity !== '0';
                    };
                    const textOf = (el) => (el?.innerText || el?.textContent || '').trim();
                    const isRateLimitText = (text) => {
                        const lowered = String(text || '').toLowerCase();
                        return phrases.some((phrase) => lowered.includes(phrase));
                    };
                    const popupSelector = [
                        '[role="dialog"]',
                        '[role="alertdialog"]',
                        '[role="alert"]',
                        '[data-testid*="toast"]',
                        '[data-testid*="modal"]',
                        '[class*="toast"]',
                        '[class*="modal"]'
                    ].join(', ');
                    const candidates = Array.from(document.querySelectorAll(popupSelector))
                        .filter((el) => visible(el) && isRateLimitText(textOf(el)));

                    // Fallback for generic fixed overlays without dialog roles.
                    if (!candidates.length) {
                        for (const node of Array.from(document.querySelectorAll('div, section, aside'))) {
                            if (!visible(node)) continue;
                            const ownText = textOf(node);
                            if (!ownText || ownText.length > 1200 || !isRateLimitText(ownText)) continue;
                            let overlay = node;
                            while (overlay && overlay !== document.body) {
                                const style = window.getComputedStyle(overlay);
                                if (['fixed', 'sticky'].includes(style.position) && visible(overlay)) {
                                    candidates.push(overlay);
                                    break;
                                }
                                overlay = overlay.parentElement;
                            }
                            if (candidates.length) break;
                        }
                    }

                    if (!candidates.length) {
                        return { detected: false, dismissed: false, text: '' };
                    }

                    const popup = candidates[0];
                    const popupText = textOf(popup);
                    const controls = Array.from(popup.querySelectorAll('button, [role="button"]'))
                        .filter(visible);
                    const safeCloseControl = controls.find((button) => {
                        const label = [
                            button.getAttribute('aria-label'),
                            button.getAttribute('title'),
                            button.getAttribute('data-testid'),
                            textOf(button)
                        ].filter(Boolean).join(' ').trim().toLowerCase();
                        if (!label) return false;
                        if (/try again|retry|resend|submit|send/.test(label)) return false;
                        return /(^|\\b)(close|dismiss|cancel|okay|ok|got it)(\\b|$)/.test(label)
                            || label.includes('modal-close')
                            || label.includes('close-button')
                            || label.includes('dismiss-button');
                    });

                    if (safeCloseControl) {
                        safeCloseControl.click();
                        return { detected: true, dismissed: true, text: popupText };
                    }

                    // Fallback for an unlabeled X icon in the upper-right corner.
                    const popupRect = popup.getBoundingClientRect();
                    const cornerClose = controls.find((button) => {
                        const r = button.getBoundingClientRect();
                        const nearTop = r.top <= popupRect.top + Math.max(64, popupRect.height * 0.2);
                        const nearRight = r.right >= popupRect.right - Math.max(64, popupRect.width * 0.2);
                        const compact = r.width <= 64 && r.height <= 64;
                        return nearTop && nearRight && compact;
                    });
                    if (cornerClose) {
                        cornerClose.click();
                        return { detected: true, dismissed: true, text: popupText };
                    }

                    return { detected: true, dismissed: false, text: popupText };
                }
                """
            )
            if isinstance(value, dict):
                return {
                    "detected": bool(value.get("detected")),
                    "dismissed": bool(value.get("dismissed")),
                    "text": str(value.get("text") or ""),
                }
        except Exception:
            pass
        return {"detected": False, "dismissed": False, "text": ""}

    @classmethod
    def _visible_rate_limit_text(cls, page) -> str:
        return str(cls._rate_limit_popup_status(page).get("text") or "")

    def _raise_if_rate_limited(self, page) -> None:
        status = self._rate_limit_popup_status(page)
        if not bool(status.get("detected")):
            return

        dismissed = bool(status.get("dismissed"))
        if not dismissed:
            try:
                page.keyboard.press("Escape")
                page.wait_for_timeout(250)
            except Exception:
                pass

        if dismissed:
            raise RuntimeError(
                "ChatGPT is rate limited. The popup was dismissed; try again after the limit clears."
            )
        raise RuntimeError(
            "ChatGPT is rate limited. No safe close control was found; try again after the limit clears."
        )

    def _wait_for_response(
        self,
        page,
        *,
        before_assistant_turns: list[dict[str, str]],
        max_wait_seconds: int,
        stop_requested: StopFn | None = None,
    ) -> str:
        max_wait_seconds = max(30, int(max_wait_seconds))
        no_progress_timeout = min(DEFAULT_NO_PROGRESS_TIMEOUT_SECONDS, max(25, max_wait_seconds // 2))
        deadline = time.monotonic() + max_wait_seconds
        last_text = ""
        last_change = time.monotonic()
        stable_since: float | None = None

        while time.monotonic() < deadline:
            if stop_requested is not None and stop_requested():
                self._click_stop(page)
                check_stop(stop_requested)
            self._raise_if_rate_limited(page)

            current = self._latest_new_response(page, before_assistant_turns).strip()
            running = self._is_generation_running(page)
            now = time.monotonic()

            if current:
                if current != last_text:
                    last_text = current
                    last_change = now
                    stable_since = None
                elif not running:
                    stable_since = stable_since or now
                    if now - stable_since >= 4.0:
                        return current
                else:
                    stable_since = None
            elif last_text and not running:
                # ChatGPT sometimes replaces a streaming DOM node just before
                # the final render. Keep observing the captured text instead of
                # treating that brief disappearance as a new empty response.
                stable_since = stable_since or now
                if now - stable_since >= 4.0:
                    return last_text
            else:
                stable_since = None

            if last_text and running and now - last_change > no_progress_timeout:
                self._click_stop(page)
                raise TimeoutError("ChatGPT response stopped making progress.")

            sleep_with_stop(0.75, stop_requested)

        # Do not use a shorter pre-output deadline. A busy ChatGPT request may
        # legitimately take the full configured timeout before rendering text.
        self._click_stop(page)
        if last_text:
            raise TimeoutError(
                f"ChatGPT response timed out after partial output ({max_wait_seconds} seconds)."
            )
        raise TimeoutError(
            f"ChatGPT response did not appear within the configured timeout ({max_wait_seconds} seconds)."
        )



def run_chatgpt_web_path(
    path: str | Path,
    max_files: int | None = 59,
    max_lines_per_batch: int = 600,
    wait_between_batches: float = MIN_POST_SAVE_DELAY_SECONDS,
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
    progress: ProgressFn | None = None,
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
    total_files = len(po_files)
    if progress is not None:
        progress(0, total_files, po_files[0])

    with ChatGPTWebSession(cdp_url=cdp_url) as session:
        for file_index, po_path in enumerate(po_files, start=1):
            check_stop(stop_requested)
            backup_created = False
            if create_missing_backups and not find_backup_for_file(po_path):
                backup_path = po_path.with_name(f"{po_path.stem} - Copy.po")
                if backup_path.exists():
                    say(f"Copy.po exists, not touched: {backup_path.name}")
                else:
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
                prompt_template=CHATGPT_TRANSLATE_PROMPT_TEMPLATE,
            )
            file_result.backup_created = backup_created
            results.append(file_result)
            if progress is not None:
                progress(file_index, total_files, po_path)

    return WebTranslateRunResult(renamed_duplicates=renamed_duplicates, files=results)
