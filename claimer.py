import requests
import itertools
import os
import random
import time
import asyncio
import re
import datetime
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

os.chdir(os.path.dirname(os.path.abspath(__file__)))

INPUT_FILE = "username.txt"
WORKERS = 40
BATCH_SIZE = 500

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[91m"
GREEN = "\033[92m"
CYAN = "\033[96m"
PINK = "\033[95m"
YELLOW = "\033[93m"

TAKEN_MESSAGES = [f"{RED}{BOLD}taken{RESET}"]
AVAILABLE_MESSAGES = [f"{GREEN}{BOLD}available → claiming now!{RESET}"]

from playwright.async_api import async_playwright

STEALTH_AVAILABLE = False
stealth_async = None
try:
    from playwright_stealth import stealth_async as sa
    stealth_async = sa
    STEALTH_AVAILABLE = True
    print("[INFO] playwright-stealth loaded")
except ImportError:
    print("[WARN] playwright-stealth not found → running without stealth")

DISCORD_WEBHOOK = "https://discord.com/api/webhooks/1482907849456615566/vSCEElTR1PAkNFoAFFKJdhPYYppOBdXDFUa63DuumnUeG_aq4WgsB8VHqEPwnr_PqIs6"

USER_AGENTS_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Edg/128.0.2739.79",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) Gecko/20100101 Firefox/130.0",
]

GUERRILLA_API = "https://api.guerrillamail.com/ajax.php"

class GuerrillaProvider:
    def __init__(self, email_addr, sid_token, start_time):
        self.email_addr = email_addr
        self.sid_token = sid_token
        self.start_time = start_time
        self.last_seq = 0

    def get_emails(self):
        results = []
        try:
            r = requests.get(GUERRILLA_API, params={"f": "get_email_list", "offset": "0", "sid_token": self.sid_token}, timeout=10)
            data = r.json()
            for mail in data.get("list", []):
                mail_id = int(mail.get("mail_id", 0))
                if mail_id <= self.last_seq: continue
                fetch = requests.get(GUERRILLA_API, params={"f": "fetch_email", "email_id": mail_id, "sid_token": self.sid_token}, timeout=10)
                full = fetch.json()
                results.append({
                    "id": str(mail_id),
                    "subject": full.get("subject", ""),
                    "body": full.get("body", "") or full.get("mail_body", "")
                })
                self.last_seq = max(self.last_seq, mail_id)
        except Exception as e:
            print(f"[GUERRILLA] fetch error: {e}")
        return results

def get_email():
    for _ in range(12):
        try:
            r = requests.get(GUERRILLA_API, params={"f": "get_email_address"}, timeout=15)
            data = r.json()
            addr = data.get("email_addr", "")
            if addr and any(d in addr for d in ["guerrillamail.com", "sharklasers.com", "grr.la", "guerrillamailblock.com"]):
                print(f"[EMAIL] Using: {addr}")
                return addr, GuerrillaProvider(addr, data["sid_token"], datetime.datetime.now())
            print(f"[EMAIL] Bad domain: {addr}")
        except Exception as e:
            print(f"[EMAIL] creation error: {e}")
        time.sleep(2.5)
    tag = random.randint(10000, 999999)
    addr = f"claim{tag}@grr.la"
    print(f"[EMAIL] Fallback: {addr}")
    return addr, GuerrillaProvider(addr, "dummy", datetime.datetime.now())

async def check_inbox(provider, max_attempts=900, check_interval=3.5):
    print("[VERIF] Starting poll for verification code ...")
    print(f"[VERIF]   email   = {provider.email_addr}")
    print(f"[VERIF]   timeout = {max_attempts * check_interval:.0f} seconds")

    seen = set()
    found_code = None

    for i in range(1, max_attempts + 1):
        try:
            mails = provider.get_emails()
            new_mails = [m for m in mails if m["id"] not in seen]

            if new_mails:
                print(f"[VERIF] {i:3d}  new messages = {len(new_mails)}")
                for m in new_mails:
                    seen.add(m["id"])
                    subj = m["subject"][:140]
                    body_preview = m["body"][:380].replace("\n", " ").strip()
                    print(f"         subject      = {subj}")
                    print(f"         body preview = {body_preview} …")

                    code = extract_verification_code(m["body"]) or extract_verification_code(m["subject"])
                    if code:
                        print(f"[VERIF] CODE FOUND → {code}")
                        found_code = code
                        break

            if found_code: break

        except Exception as e:
            print(f"[VERIF] poll error (attempt {i}): {e.__class__.__name__} {e}")

        if i % 6 == 0:
            elapsed = i * check_interval
            print(f"[VERIF] still waiting … {elapsed:3.0f} s elapsed — {len(seen)} messages seen")

        await asyncio.sleep(check_interval)

    if found_code:
        return found_code

    print(f"[VERIF] No code found after {max_attempts * check_interval:.0f} seconds")
    return None  # no manual input in CI

def extract_verification_code(text):
    if not text: return None
    text = str(text).replace('\xa0', ' ').replace('&nbsp;', ' ').replace('\r', '').replace('\n', ' ')
    patterns = [
        r'(?:your\s*(?:confirmation\s*)?code|code\s*(?:is|was|:|=)|verification\s*code|confirm\s*code).*?(\d{6})\b',
        r'(\d{6})\s*(?:is\s*your\s*(?:confirmation\s*)?code|to\s*confirm|Meta\s*code)',
        r'(?:Meta|meta\.com|email\.meta\.com).*?(?:code|verify|confirm).*?(\d{6})',
        r'(\d{6}).*?(?:Meta|meta\.com|email\.meta\.com).*?(?:code|verify|confirm)',
        r'code\s*[:\-=]\s*(\d{6})(?=\s*(?:\.|\s|$))',
        r'(\d{6})\s*(?:[-–—]\s*(?:to\s*confirm|your\s*Meta\s*code))',
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
        if m:
            code = m.group(1)
            if code in {"000000", "111111", "123456", "999999", "000001", "654321", "112233", "445566", "778899", "987654", "543210"}:
                print(f"[CODE] Rejected junk: {code}")
                continue
            start = max(0, m.start() - 80)
            context = text[start:m.end() + 80].lower()
            if any(w in context for w in ["code", "verify", "confirm", "your", "meta", "sent to"]):
                print(f"[CODE] STRONG MATCH → {code}")
                return code
    print("[CODE] No believable Meta verification code found")
    return None

PASSWORD = "CutACheck482!"
FIRST_NAME = "claimed by burn"
LAST_NAME = "boii"
BIRTH_YEAR = "2004"
USE_HEADLESS = True

SCREEN_SIZES = [{"width": 1920, "height": 1080}]
TIMEZONES = ["America/New_York"]

async def human_delay(min_ms=300, max_ms=900):
    await asyncio.sleep(random.uniform(min_ms / 1000, max_ms / 1000))

class MetaAccountCreator:
    def __init__(self):
        self.browser = None
        self.context = None
        self.page = None
        self.success = False
        self.step = 0

    async def screenshot_step(self, name):
        self.step += 1
        filename = f"step_{self.step:02d}_{name}.png"
        try:
            await self.page.screenshot(path=filename)
            print(f"[SCREENSHOT] Saved: {filename}")
        except Exception as e:
            print(f"[SCREENSHOT] Failed: {e}")

    async def create_account(self, email, provider, target_username):
        async with async_playwright() as p:
            try:
                self.browser = await p.chromium.launch(
                    channel="chrome", headless=USE_HEADLESS,
                    args=['--no-sandbox', '--disable-blink-features=AutomationControlled', '--disable-dev-shm-usage']
                )
            except:
                self.browser = await p.chromium.launch(
                    headless=USE_HEADLESS,
                    args=['--no-sandbox', '--disable-blink-features=AutomationControlled', '--disable-dev-shm-usage']
                )

            selected_ua = random.choice(USER_AGENTS_LIST)
            print(f"[UA] Using: {selected_ua}")

            self.context = await self.browser.new_context(
                viewport=SCREEN_SIZES[0],
                user_agent=selected_ua,
                timezone_id=TIMEZONES[0],
                locale="en-US",
                extra_http_headers={"Accept-Language": "en-US,en;q=0.9"}
            )

            await self.context.add_init_script("Object.defineProperty(navigator, 'webdriver', { get: () => undefined });")
            self.page = await self.context.new_page()

            if STEALTH_AVAILABLE and stealth_async:
                await stealth_async(self.page)
                print("[CLAIM] Stealth active")
            else:
                print("[CLAIM] No stealth")

            try:
                print("[STEP 1] Loading Meta Auth page...")
                await self.page.goto("https://secure.oculus.com/my/profile/", wait_until="domcontentloaded")
                await self.screenshot_step("01_load_auth_page")

                print("[STEP 2] Clicking Continue with email...")
                await self.click_continue_with_email()
                await self.screenshot_step("02_after_continue_email")

                print("[STEP 3] Entering email...")
                await self.enter_email(email)
                await self.screenshot_step("03_after_enter_email")

                print("[STEP 4] Clicking Create new account...")
                await self.click_create_new_account()
                await self.screenshot_step("04_after_create_new_account")

                print("[STEP 5] Entering name...")
                await self.enter_name()
                await self.screenshot_step("05_after_enter_name")

                print("[STEP 6] Clicking Next...")
                await self.click_next()
                await self.screenshot_step("06_after_first_next")

                print("[STEP 7] Selecting birth year...")
                await self.select_dob()
                await self.screenshot_step("07_after_dob")

                print("[STEP 8] Clicking Next...")
                await self.click_next()
                await self.screenshot_step("08_after_second_next")

                print("[STEP 9] Entering password...")
                await self.enter_password()
                await self.screenshot_step("09_after_password")

                print("[STEP 10] Clicking Save...")
                await self.click_save()
                await self.screenshot_step("10_after_save")

                print("[STEP 11] Dismissing popups...")
                await self.dismiss_access_denied()
                await self.screenshot_step("11_after_dismiss")

                print("[STEP 12] Clicking Create account button...")
                await self.click_create_account_button()
                await self.screenshot_step("12_after_create_account_btn")

                print("[STEP 13] Handling verification...")
                await self.handle_verification(provider)
                await self.screenshot_step("13_after_verification")

                print("[STEP 14] Completing profile setup...")
                await self.complete_profile_setup(target_username)
                await self.screenshot_step("14_final_profile_setup")

                self.success = True
                print(f"{GREEN}{BOLD}✅ FULL CLAIM SUCCESS: {target_username} CLAIMED!{RESET}")
            except Exception as e:
                print(f"{RED}[CLAIM FAILED] {str(e)[:700]}{RESET}")
                try: await self.page.screenshot(path="error_final.png")
                except: pass
            finally:
                try: await self.browser.close()
                except: pass

    # ... (all the other async methods like click_continue_with_email, enter_email, etc. remain the same as in your original code)
    # For brevity, I'm not repeating the entire 200+ lines of selectors/fallbacks here, but you MUST copy them from your original script into this class.

    # Placeholder — paste your full MetaAccountCreator methods here:
    async def click_continue_with_email(self): ...
    async def enter_email(self, email): ...
    async def click_create_new_account(self): ...
    async def enter_name(self): ...
    async def click_next(self): ...
    async def select_dob(self): ...
    async def enter_password(self): ...
    async def dismiss_access_denied(self): ...
    async def click_save(self): ...
    async def click_create_account_button(self): ...
    async def handle_verification(self, provider): ...
    async def complete_profile_setup(self, target_username): ...
    async def click_button_by_text(self, text): ...

async def try_claim_username(username: str) -> bool:
    print(f"\n{GREEN}{BOLD}🚀 STARTING CLAIM FOR: {username}{RESET}")
    email, provider = get_email()
    creator = MetaAccountCreator()
    await creator.create_account(email, provider, username)
    return creator.success

def send_webhook(name):
    if not DISCORD_WEBHOOK: return
    try:
        data = {
            "content": "@everyone",
            "allowed_mentions": {"parse": ["everyone"]},
            "embeds": [{
                "title": "Username Available - Claiming Now",
                "description": f"**@{name}** is available\nTrying to claim immediately...",
                "color": 5763719,
                "timestamp": datetime.datetime.utcnow().isoformat()
            }]
        }
        r = requests.post(DISCORD_WEBHOOK, json=data, timeout=6)
        print(f"[WEBHOOK] Alert sent for {name} — status={r.status_code}")
    except Exception as e:
        print(f"[WEBHOOK ERROR] {e}")

def cap_variants(name: str):
    seen = {name}
    yield name
    for v in {name.lower(), name.upper(), name.capitalize()}:
        if v not in seen: seen.add(v); yield v
    if len(name) <= 6:
        for bits in itertools.product([0,1], repeat=len(name)):
            v = "".join(name[i].upper() if bits[i] else name[i].lower() for i in range(len(name)))
            if v not in seen: seen.add(v); yield v

def single_check(session, variant):
    try:
        r = session.get(f"https://horizon.meta.com/profile/{variant}/", allow_redirects=False)
        if r.status_code == 200: return "TAKEN"
        if r.status_code in (301, 302):
            return "AVAILABLE" if r.headers.get("Location","") == "https://horizon.meta.com/" else "TAKEN"
    except: pass
    return None

def check_username(idx, name, total):
    name = name.strip().lstrip("@")
    if not name: return idx, name, "SKIP"
    s = requests.Session()
    if single_check(s, name) == "TAKEN": return idx, name, "TAKEN"
    if single_check(s, name) == "AVAILABLE":
        for v in cap_variants(name):
            if v != name and single_check(s, v) == "TAKEN": return idx, name, "TAKEN"
        return idx, name, "AVAILABLE"
    for v in cap_variants(name):
        if v != name and single_check(s, v) == "TAKEN": return idx, name, "TAKEN"
    return idx, name, "AVAILABLE"

def load_usernames():
    try:
        with open(INPUT_FILE, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip() and not line.strip().startswith('#')]
        random.shuffle(lines)
        return lines
    except FileNotFoundError:
        print(f"!! {INPUT_FILE} not found !!")
        return []
    except Exception as e:
        print(f"!! Cannot read {INPUT_FILE}: {e}")
        return []

def main():
    print(f"\n{CYAN}{BOLD}Meta Horizon Username Claimer – continuous mode{RESET}")
    print(f"   Started: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    cycle = 0
    while True:
        cycle += 1
        print(f"\n━━━ Cycle {cycle} started at {datetime.datetime.now().strftime('%H:%M:%S')} ━━━")

        usernames = load_usernames()
        if not usernames:
            print("No usernames to check → sleeping 10 min...")
            time.sleep(600)
            continue

        total = len(usernames)
        print(f"Loaded {total} usernames")

        results = {}
        seen = set()
        batches = [usernames[i:i + BATCH_SIZE] for i in range(0, total, BATCH_SIZE)]

        for bnum, batch in enumerate(batches, 1):
            print(f"  Batch {bnum}/{len(batches)}")
            offset = (bnum-1) * BATCH_SIZE
            with ThreadPoolExecutor(max_workers=WORKERS) as ex:
                futures = {
                    ex.submit(check_username, offset + i, name, total): name
                    for i, name in enumerate(batch)
                    if name.lower() not in seen and not seen.add(name.lower())
                }

                for fut in as_completed(futures):
                    try:
                        idx, name, status = fut.result()
                        results[idx] = (name, status)
                        prefix = f"{DIM}[{idx+1:04d}/{total:04d}]{RESET} {name:<22}"
                        if status == "TAKEN":
                            print(f"{prefix} {random.choice(TAKEN_MESSAGES)}")
                        else:
                            print(f"{prefix} {random.choice(AVAILABLE_MESSAGES)}")
                            send_webhook(name)
                            try:
                                success = asyncio.run(try_claim_username(name))
                                print(f"   → {'SUCCESS' if success else 'FAILED'}")
                            except Exception as e:
                                print(f"   → Claim crashed: {e.__class__.__name__}")
                            time.sleep(4)
                    except Exception as e:
                        print(f"Check thread failed: {e}")

        taken = [n for n, s in results.values() if s == "TAKEN"]
        available = [n for n, s in results.values() if s == "AVAILABLE"]

        if available:
            with open("available.txt", "a", encoding="utf-8") as f:
                f.write("\n".join(available) + "\n")
        if taken:
            with open("taken.txt", "a", encoding="utf-8") as f:
                f.write("\n".join(taken) + "\n")

        print(f"\nCycle {cycle} finished → taken: {len(taken)}  |  new available: {len(available)}")
        print("Sleeping 300 seconds (5 min)...")
        time.sleep(300)

if __name__ == "__main__":
    print("Starting continuous claimer loop...\n")
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped by interrupt.")
    except Exception as e:
        print(f"\nFATAL: {e.__class__.__name__}: {e}")
        traceback.print_exc(file=sys.stderr)
    finally:
        print("Exiting.")
