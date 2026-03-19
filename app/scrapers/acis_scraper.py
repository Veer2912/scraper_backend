import asyncio
import json
import logging
import math
import os
import random
import re
import tempfile
from typing import Optional

import nodriver as uc
from nodriver import cdp

from app.config import (
    ACIS_URL,
    NODRIVER_PROFILE_PATH,
    SCRAPER_HEADLESS,
    KEEP_BROWSER_OPEN_SECONDS,
    SCRAPER_USER_AGENT,
    RESULT_WAIT_ATTEMPTS,
    RESULT_WAIT_SLEEP_SECONDS,
    CLOUDFLARE_WAIT_ATTEMPTS,
    CLOUDFLARE_WAIT_SLEEP_SECONDS,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stealth / anti-detection helpers
# ---------------------------------------------------------------------------

_STEALTH_JS = """
(function () {
    // 1. Hide navigator.webdriver
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

    // 2. Spoof plugins so the browser looks real
    Object.defineProperty(navigator, 'plugins', {
        get: () => {
            const arr = [
                { name: 'Chrome PDF Plugin',   filename: 'internal-pdf-viewer' },
                { name: 'Chrome PDF Viewer',   filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
                { name: 'Native Client',       filename: 'internal-nacl-plugin' },
            ];
            arr.__proto__ = PluginArray.prototype;
            return arr;
        }
    });

    // 3. Spoof languages
    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });

    // 4. Prevent iframe contentWindow.navigator.webdriver leaks
    const origIframe = HTMLIFrameElement.prototype.__lookupGetter__('contentWindow');
    Object.defineProperty(HTMLIFrameElement.prototype, 'contentWindow', {
        get: function () {
            const win = origIframe.call(this);
            try {
                Object.defineProperty(win.navigator, 'webdriver', { get: () => undefined });
            } catch (_) {}
            return win;
        }
    });

    // 5. Realistic chrome runtime object
    if (!window.chrome) {
        window.chrome = { runtime: {} };
    }

    // 6. Permission query spoof (hCaptcha probes this)
    const origQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (params) =>
        params.name === 'notifications'
            ? Promise.resolve({ state: Notification.permission })
            : origQuery(params);
})();
"""


async def inject_stealth(page):
    """Inject anti-detection JS into the current page context."""
    try:
        await page.evaluate(_STEALTH_JS)
    except Exception as e:
        logger.warning(f"Stealth inject warning: {e}")


def _bezier(t, p0, p1, p2, p3):
    """Cubic Bezier point at parameter t."""
    return (
        (1 - t) ** 3 * p0
        + 3 * (1 - t) ** 2 * t * p1
        + 3 * (1 - t) * t ** 2 * p2
        + t ** 3 * p3
    )


async def human_move(page, x1: float, y1: float, x2: float, y2: float,
                     steps: int = None):
    """
    Move the mouse from (x1,y1) to (x2,y2) along a randomised cubic Bezier
    curve, mimicking organic hand movement.
    """
    if steps is None:
        dist = math.hypot(x2 - x1, y2 - y1)
        steps = max(20, int(dist / 5))

    cp1x = x1 + random.uniform(-80, 80) + (x2 - x1) * 0.3
    cp1y = y1 + random.uniform(-80, 80) + (y2 - y1) * 0.3
    cp2x = x1 + random.uniform(-80, 80) + (x2 - x1) * 0.7
    cp2y = y1 + random.uniform(-80, 80) + (y2 - y1) * 0.7

    for i in range(steps + 1):
        t = i / steps
        t_eased = t * t * (3 - 2 * t)
        mx = _bezier(t_eased, x1, cp1x, cp2x, x2)
        my = _bezier(t_eased, y1, cp1y, cp2y, y2)

        await page.send(
            cdp.input_.dispatch_mouse_event(
                type_="mouseMoved",
                x=round(mx, 1),
                y=round(my, 1),
            )
        )
        await asyncio.sleep(random.uniform(0.005, 0.018))


async def human_click(page, element, jitter: int = 4):
    """
    Move the mouse naturally to an element then click it via CDP.
    """
    end_x, end_y = None, None

    if isinstance(element, str):
        try:
            rect = await page.evaluate(f"""
                (function() {{
                    const el = document.querySelector({json.dumps(element)});
                    if (!el) return null;
                    const r = el.getBoundingClientRect();
                    return {{ x: r.left, y: r.top, w: r.width, h: r.height }};
                }})()
            """)
            if rect:
                end_x = rect["x"] + rect["w"] / 2 + random.uniform(-jitter, jitter)
                end_y = rect["y"] + rect["h"] / 2 + random.uniform(-jitter, jitter)
        except Exception:
            pass
    else:
        try:
            box = await element.get_position()
            end_x = box.x + box.width / 2 + random.uniform(-jitter, jitter)
            end_y = box.y + box.height / 2 + random.uniform(-jitter, jitter)
        except Exception:
            pass

    if end_x is None:
        try:
            await element.click()
        except Exception:
            pass
        return

    start_x = random.uniform(200, 800)
    start_y = random.uniform(200, 600)

    await human_move(page, start_x, start_y, end_x, end_y)
    await asyncio.sleep(random.uniform(0.08, 0.18))

    await page.send(
        cdp.input_.dispatch_mouse_event(
            type_="mousePressed",
            x=round(end_x, 1),
            y=round(end_y, 1),
            button=cdp.input_.MouseButton.LEFT,
            click_count=1,
        )
    )
    await asyncio.sleep(random.uniform(0.06, 0.14))
    await page.send(
        cdp.input_.dispatch_mouse_event(
            type_="mouseReleased",
            x=round(end_x, 1),
            y=round(end_y, 1),
            button=cdp.input_.MouseButton.LEFT,
            click_count=1,
        )
    )


async def warm_up_mouse(page, seconds: float = 2.0):
    """
    Randomly wander the mouse to build up movement history.
    """
    logger.info("Warming up mouse movement history...")
    end_time = asyncio.get_event_loop().time() + seconds
    cx, cy = random.uniform(300, 700), random.uniform(200, 500)
    while asyncio.get_event_loop().time() < end_time:
        nx = cx + random.uniform(-150, 150)
        ny = cy + random.uniform(-100, 100)
        nx = max(50, min(nx, 1200))
        ny = max(50, min(ny, 700))
        await human_move(page, cx, cy, nx, ny, steps=random.randint(10, 20))
        cx, cy = nx, ny
        await asyncio.sleep(random.uniform(0.05, 0.2))


def ensure_profile_path() -> None:
    os.makedirs(NODRIVER_PROFILE_PATH, exist_ok=True)


async def handle_cloudflare(page) -> bool:
    """Wait for site gate / Cloudflare until the I Accept button appears."""
    logger.info("Monitoring page for Cloudflare challenges...")
    accept_button_xpath = "/html/body/div[6]/div/div/div/div/div[1]/button"

    for i in range(CLOUDFLARE_WAIT_ATTEMPTS):
        try:
            current_url = page.url
            current_title = await page.evaluate("document.title")
            logger.info("Step %s: URL=%s | Title='%s'", i + 1, current_url, current_title)

            accept_xpath_js = json.dumps(accept_button_xpath)
            success = await page.evaluate(f"""
                document.evaluate(
                    {accept_xpath_js},
                    document,
                    null,
                    XPathResult.FIRST_ORDERED_NODE_TYPE,
                    null
                ).singleNodeValue !== null
            """)
            if success:
                logger.info("Success! Page content found ('I Accept' button detected).")
                return True

            source = (await page.get_content()).lower()
            if "verify you are human" in source or "cf-challenge" in source:
                logger.warning("Cloudflare challenge visible.")
            elif "checking your browser" in source:
                logger.info("Cloudflare is checking your browser... waiting...")
        except Exception:
            pass

        await asyncio.sleep(CLOUDFLARE_WAIT_SLEEP_SECONDS)

    return False


async def handle_hcaptcha(page) -> bool:
    """
    Check for hCaptcha and try to solve it via natural interaction.
    """
    logger.info("Checking for hCaptcha...")

    captcha_present = await page.evaluate('''
        (function() {
            const frames = Array.from(document.querySelectorAll('iframe'));
            const f = frames.find(fr => fr.src && fr.src.includes('hcaptcha.com'));
            if (!f) return 'none';
            const t = document.querySelector('textarea[name="h-captcha-response"]');
            if (t && t.value && t.value.length > 10) return 'solved';
            return 'present';
        })()
    ''')

    if captcha_present == 'none':
        logger.info("No hCaptcha detected.")
        return True
    if captcha_present == 'solved':
        logger.info("hCaptcha already solved.")
        return True

    logger.info("hCaptcha present — warming up mouse, then clicking checkbox...")

    await warm_up_mouse(page, seconds=random.uniform(2.5, 4.0))
    await asyncio.sleep(random.uniform(0.6, 1.4))

    try:
        cb_iframe = await page.select('iframe[src*="hcaptcha.com"][src*="checkbox"]')
        if not cb_iframe:
            cb_iframe = await page.select('iframe[src*="hcaptcha.com"]')
        if cb_iframe:
            await human_click(page, cb_iframe)
            logger.info("Clicked hCaptcha checkbox with human movement.")
    except Exception as e:
        logger.warning(f"Checkbox click error: {e}")

    await asyncio.sleep(2)

    for attempt in range(60):
        try:
            state = await page.evaluate('''
                (function() {
                    const t = document.querySelector('textarea[name="h-captcha-response"]');
                    if (t && t.value && t.value.length > 10) return 'passed';

                    const challenge = document.querySelector(
                        'iframe[src*="hcaptcha.com"][src*="challenge"]'
                    );
                    if (challenge && challenge.offsetParent !== null) return 'challenge';

                    const checkbox = document.querySelector('iframe[src*="hcaptcha.com"]');
                    if (!checkbox) return 'passed';

                    return 'waiting';
                })()
            ''')

            if state == 'passed':
                logger.info("hCaptcha passed silently (no challenge shown).")
                return True
            if state == 'challenge':
                if attempt == 0:
                    logger.warning("hCaptcha visual challenge appeared — please solve it manually.")
        except Exception as e:
            logger.warning(f"hCaptcha poll error: {e}")

        await asyncio.sleep(2)

    logger.error("hCaptcha not solved within timeout.")
    return False


def normalize_lines(page_text: str) -> list[str]:
    lines = []
    for line in page_text.splitlines():
        cleaned = re.sub(r"[ \t]+", " ", line.replace("\xa0", " ")).strip()
        if cleaned:
            lines.append(cleaned)
    return lines


def section_text(lines: list[str], start_label: str, end_labels: list[str]) -> Optional[str]:
    try:
        start_idx = lines.index(start_label)
    except ValueError:
        return None

    end_idx = len(lines)
    for i in range(start_idx + 1, len(lines)):
        if lines[i] in end_labels:
            end_idx = i
            break

    block = "\n".join(lines[start_idx:end_idx]).strip()
    return block if block else None


def value_after_label(lines: list[str], label: str) -> Optional[str]:
    for i, line in enumerate(lines):
        if line == label and i + 1 < len(lines):
            return lines[i + 1]
    return None


def values_after_label_until(lines: list[str], label: str, stop_labels: list[str]) -> Optional[str]:
    try:
        start_idx = lines.index(label)
    except ValueError:
        return None

    values = []
    for i in range(start_idx + 1, len(lines)):
        if lines[i] in stop_labels:
            break
        values.append(lines[i])

    if not values:
        return None
    return ", ".join(values)


def parse_case_text(page_text: str) -> dict:
    lines = normalize_lines(page_text)
    full_text = "\n".join(lines)

    start_marker = "Automated Case Information"
    end_marker = "Archive"

    start_idx = None
    end_idx = None

    for i, line in enumerate(lines):
        if line == start_marker:
            if "Name:" in "\n".join(lines[i:i + 20]):
                start_idx = i
                break

    if start_idx is not None:
        for j in range(start_idx + 1, len(lines)):
            if lines[j] == end_marker:
                end_idx = j
                break

    aci_block = "\n".join(lines[start_idx:end_idx]).strip() if start_idx is not None else full_text

    name = value_after_label(lines, "Name:")
    a_number = value_after_label(lines, "A-Number:")
    docket_date = value_after_label(lines, "Docket Date:")

    hearing_line = None
    hearing_type = None
    hearing_mode = None
    hearing_date = None
    hearing_time = None
    hearing_datetime = None

    for line in lines:
        if line.startswith("Your upcoming ") and " hearing is " in line:
            hearing_line = line
            break

    if hearing_line:
        m = re.search(
            r"Your upcoming\s+(?P<hearing_type>.+?)\s+hearing is\s+(?P<hearing_mode>.+?)\s+on\s+(?P<hearing_datetime>.+?)\.",
            hearing_line,
            re.IGNORECASE
        )
        if m:
            hearing_type = m.group("hearing_type").strip()
            hearing_mode = m.group("hearing_mode").strip()
            hearing_datetime = m.group("hearing_datetime").strip()

            dt_match = re.match(
                r"(?P<hearing_date>.+?)\s+at\s+(?P<hearing_time>.+)",
                hearing_datetime,
                re.IGNORECASE
            )
            if dt_match:
                hearing_date = dt_match.group("hearing_date").strip()
                hearing_time = dt_match.group("hearing_time").strip()
            else:
                hearing_date = hearing_datetime

    judge = value_after_label(lines, "JUDGE")

    next_hearing_court_address = values_after_label_until(
        lines,
        "COURT ADDRESS",
        ["Court Decision and Motion Information", "PHONE NUMBER", "BIA Case Information", "Court Contact Information"]
    )

    court_decision_block = section_text(
        lines,
        "Court Decision and Motion Information",
        ["BIA Case Information", "Court Contact Information"]
    )
    court_decision = None
    if court_decision_block:
        decision_lines = normalize_lines(court_decision_block)
        if len(decision_lines) >= 2:
            court_decision = " ".join(decision_lines[1:]).strip()

    bia_case_block = section_text(
        lines,
        "BIA Case Information",
        ["Court Contact Information"]
    )
    bia_case_info = None
    if bia_case_block:
        bia_lines = normalize_lines(bia_case_block)
        if len(bia_lines) >= 2:
            bia_case_info = " ".join(bia_lines[1:]).strip()

    court_contact_block = section_text(
        lines,
        "Court Contact Information",
        ["Archive"]
    )

    contact_address = None
    phone_number = None
    if court_contact_block:
        contact_lines = normalize_lines(court_contact_block)

        contact_address = values_after_label_until(
            contact_lines,
            "COURT ADDRESS",
            ["PHONE NUMBER"]
        )
        phone_number = value_after_label(contact_lines, "PHONE NUMBER")

    result = {
        "a_number": a_number,
        "name": name,
        "docket_date": docket_date,
        "hearing_type": hearing_type,
        "hearing_mode": hearing_mode,
        "hearing_date": hearing_date,
        "hearing_time": hearing_time,
        "hearing_datetime": hearing_datetime,
        "hearing_line": hearing_line,
        "judge": judge,
        "court_address": next_hearing_court_address,
        "court_decision": court_decision,
        "bia_case_info": bia_case_info,
        "phone_number": phone_number,
        "court_contact_address": contact_address,
        "automated_case_information_text": aci_block
    }

    return result


async def click_i_accept(page) -> None:
    logger.info("Automation: Clicking I Accept...")
    try:
        accept_btn = await page.find("I Accept", best_match=True)
        if accept_btn:
            await human_click(page, accept_btn)
            logger.info("I Accept button clicked with human movement.")
        else:
            # JS fallback
            await page.evaluate(r'''
                (function() {
                    const b = Array.from(document.querySelectorAll("button"))
                        .find(b => b.innerText.includes("I Accept") || b.innerText.includes("ACCEPT"));
                    if (b) b.click();
                })()
            ''')
            logger.warning("I Accept clicked via JS fallback.")
    except Exception as e:
        logger.warning("Error clicking I Accept: %s", e)

    await asyncio.sleep(random.uniform(2.5, 4.0))


async def enter_anumber(page, a_number: str) -> None:
    logger.info("Automation: Entering A-number: %s", a_number)
    inputs = await page.select_all(".react-code-input input")
    if len(inputs) != 9:
        raise RuntimeError(f"Expected 9 A-number inputs, found {len(inputs)}")

    for i, digit in enumerate(a_number):
        await inputs[i].click()
        await asyncio.sleep(random.uniform(0.05, 0.15))
        await inputs[i].send_keys(digit)
        await asyncio.sleep(random.uniform(0.08, 0.25))

    logger.info("A-number entered.")
    await asyncio.sleep(2)


async def select_nationality(page, nationality: str) -> None:
    logger.info("Automation: Selecting Nationality (%s)...", nationality)

    nationality_input = await page.select("input[id*='select']")
    if not nationality_input:
        raise RuntimeError("Nationality input not found.")

    await nationality_input.click()
    await asyncio.sleep(1)

    await nationality_input.send_keys(nationality)
    logger.info("Typed '%s', waiting for dropdown options...", nationality)
    await asyncio.sleep(2)

    wanted = nationality.upper()
    wanted_js = json.dumps(wanted)

    selection_made = await page.evaluate(f"""
        (function() {{
            const wanted = {wanted_js};
            const options = Array.from(
                document.querySelectorAll("[id*='option'], div[role='option'], .select__option")
            );

            let target = options.find(o => o.innerText.trim().toUpperCase() === wanted + " (IN)");
            if (!target) {{
                target = options.find(o => o.innerText.trim().toUpperCase() === wanted);
            }}
            if (!target) {{
                target = options.find(o => {{
                    const txt = o.innerText.toUpperCase();
                    return txt.includes(wanted) && !txt.includes("BRITISH");
                }});
            }}

            if (target) {{
                target.click();
                return target.innerText;
            }}
            return null;
        }})()
    """)

    if selection_made:
        logger.info("Nationality '%s' selected from dropdown.", selection_made)
    else:
        logger.warning("Could not find nationality in dropdown via JS, trying Enter key fallback...")
        await nationality_input.send_keys("\uE007")

    await asyncio.sleep(1.5)
    logger.info("Nationality selection complete.")


async def click_submit(page) -> None:
    logger.info("Automation: Clicking Submit...")

    submit_btn = await page.select("#btn_submit")
    if not submit_btn:
        raise RuntimeError("Submit button (#btn_submit) not found.")

    await page.evaluate("document.querySelector('#btn_submit').scrollIntoView({block: 'center'})")
    await asyncio.sleep(random.uniform(0.4, 0.8))
    await human_click(page, submit_btn)
    logger.info("Submit button clicked with human movement.")
    await asyncio.sleep(3)


async def wait_for_results(page) -> str:
    logger.info("Waiting for results page to load...")
    page_text = None

    for attempt in range(RESULT_WAIT_ATTEMPTS):
        try:
            page_text = await page.evaluate("""
                document.body ? document.body.innerText : ''
            """)

            if (
                page_text
                and "Automated Case Information" in page_text
                and "Name:" in page_text
                and "A-Number:" in page_text
            ):
                logger.info("Results page loaded (attempt %s).", attempt + 1)
                return page_text
        except Exception as e:
            logger.warning("Results check failed on attempt %s: %s", attempt + 1, e)

        await asyncio.sleep(RESULT_WAIT_SLEEP_SECONDS)

    html = await page.get_content()
    logger.warning("Results page did not load within the timeout.")
    logger.info("HTML SNIPPET:\n%s", html[:12000])
    raise RuntimeError("Results page did not load within timeout.")


async def scrape_case_data(a_number: str, nationality: str = "INDIA") -> dict:
    logger.info("Starting browser to navigate to: %s", ACIS_URL)
    ensure_profile_path()

    browser = None

    try:
        browser_args = [
            "--window-size=1920,1080",
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--no-first-run",
            "--no-default-browser-check",
            "--lang=en-US",
            "--accept-lang=en-US,en;q=0.9",
            f"--user-agent={SCRAPER_USER_AGENT}",
        ]

        if SCRAPER_HEADLESS:
            browser_args.insert(0, "--headless=new")

        browser = await uc.start(
            user_data_dir=NODRIVER_PROFILE_PATH,
            browser_args=browser_args
        )

        page = await browser.get(ACIS_URL)

        if page.url == "about:blank":
            await page.get(ACIS_URL)
            await asyncio.sleep(2)

        # Inject stealth patches early
        await inject_stealth(page)

        if not await handle_cloudflare(page):
            logger.warning("Could not confirm page load, but proceeding anyway...")

        # Re-inject after potential Cloudflare redirect
        await inject_stealth(page)

        await click_i_accept(page)
        await enter_anumber(page, a_number)
        await select_nationality(page, nationality)

        # Handle hCaptcha before submit
        await handle_hcaptcha(page)

        await click_submit(page)

        post_submit_text = await page.evaluate("""
            document.body ? document.body.innerText : ''
        """)
        logger.info("VISIBLE TEXT AFTER SUBMIT:\n%s", post_submit_text[:5000])

        page_text = await wait_for_results(page)
        case_data = parse_case_text(page_text)

        if not case_data.get("a_number") or not case_data.get("name"):
            raise RuntimeError("Parsed result is incomplete.")

        logger.info("Final extracted case data for %s: %s", a_number, case_data.get("name"))

        if KEEP_BROWSER_OPEN_SECONDS > 0:
            logger.info("Keeping browser open for %s seconds...", KEEP_BROWSER_OPEN_SECONDS)
            await asyncio.sleep(KEEP_BROWSER_OPEN_SECONDS)

    except Exception as e:
        logger.error("Critical scrape error: %s", e)
        raise

    finally:
        if browser:
            try:
                browser.stop()
                logger.info("Browser closed.")
            except Exception as e:
                logger.info("Browser already stopped or could not be stopped: %s", e)

    return case_data
