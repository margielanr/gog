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

# Change working directory to script location
os.chdir(os.path.dirname(os.path.abspath(__file__)))

INPUT_FILE = "username.txt"
WORKERS = 40           # lowered from 100 – GitHub runners are not that powerful
BATCH_SIZE = 500       # lowered from 1000

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

# ─── Stealth (optional) ───────────────────────────────────────
STEALTH_AVAILABLE = False
stealth_async = None
try:
    from playwright_stealth import stealth_async as sa
    stealth_async = sa
    STEALTH_AVAILABLE = True
    print("[INFO] playwright-stealth loaded")
except ImportError:
    print("[WARN] playwright-stealth not found → running without stealth")

# ─── Discord Webhook ──────────────────────────────────────────
DISCORD_WEBHOOK = "https://discord.com/api/webhooks/1482907849456615566/vSCEElTR1PAkNFoAFFKJdhPYYppOBdXDFUa63DuumnUeG_aq4WgsB8VHqEPwnr_PqIs6"

# ─── Modern User-Agents ───────────────────────────────────────
USER_AGENTS_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Edg/128.0.2739.79",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) Gecko/20100101 Firefox/130.0",
]

# ─── Guerrilla Mail ───────────────────────────────────────────
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
                if mail_id <= self.last_seq:
                    continue
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

    # fallback
    tag = random.randint(10000, 999999)
    addr = f"claim{tag}@grr.la"
    print(f"[EMAIL] Fallback: {addr}")
    return addr, GuerrillaProvider(addr, "dummy", datetime.datetime.now())

# ─── rest of your original functions remain mostly unchanged ──
# (check_inbox, extract_verification_code, MetaAccountCreator class, etc.)

# Paste here all the remaining code from your original script:
# - extract_verification_code
# - MetaAccountCreator class (the big one)
# - send_webhook
# - cap_variants
# - single_check
# - check_username
# - try_claim_username
# - main  (but modified – see below)

# ─── Modified main ─────────────────────────────────────────────

def load_usernames():
    try:
        with open(INPUT_FILE, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip() and not line.strip().startswith('#')]
        random.shuffle(lines)  # optional: randomize order each cycle
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
