# monitor_rdv_conciliateur_email.py
# Surveille la page de prise de rendez-vous des conciliateurs à Paris
# Envoie un email quand un créneau apparaît

import asyncio
import os
import random
import re
import smtplib
import ssl
import sys
import time
from email.mime.text import MIMEText
from pathlib import Path
from typing import Tuple

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from playwright.async_api import async_playwright

URL = (
    "https://rdvdaj.apps.paris.fr/rdvdaj/jsp/site/Portal.jsp"
    "?page=appointmentsearch&view=search&category=C_Conciliateur"
)

# Intervalle moyen entre deux vérifications
BASE_POLL_SEC = 45
# Petite gigue pour ne pas interroger à rythme fixe
JITTER_SEC = 25

# Fichiers locaux
STATE_FILE = Path("rdv_seen_state.txt")
LOG_FILE = Path("rdv_monitor.log")

# Message officiel d’indisponibilité
NEGATIVE_PHRASE_FULL = (
    " Aucun rendez-vous n'est actuellement disponible."
    " De nouveaux rendez-vous seront proposés prochainement sur cette page."
)

# Marqueurs génériques au cas où le texte évoluerait
NEGATIVE_MARKERS = [
    "aucun rendez-vous disponible",
    "pas de rendez-vous disponible",
    "aucune plage disponible",
]
POSITIVE_MARKERS = [
    "rendez-vous disponible",
    "plages disponibles",
    "prendre rendez-vous",
    "je prends rendez-vous",
    "créneau disponible",
    "réserver",
]

def normalize_text(s: str) -> str:
    s = s.replace("\u00A0", " ")
    s = s.replace("\u202F", " ")
    s = s.replace("\u2019", "'")
    s = s.replace("\u2018", "'")
    s = re.sub(r"\s+", " ", s, flags=re.S)
    return s.strip().lower()

def log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def load_state() -> str:
    if STATE_FILE.exists():
        try:
            return STATE_FILE.read_text(encoding="utf-8").strip()
        except Exception:
            return ""
    return ""

def save_state(s: str) -> None:
    try:
        STATE_FILE.write_text(s, encoding="utf-8")
    except Exception:
        pass

def send_email(subject: str, body: str) -> None:
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER")
    pwd = os.getenv("SMTP_PASS")
    to_addr = os.getenv("EMAIL_TO")
    if not all([host, port, user, pwd, to_addr]):
        log("Config email incomplète. Aucune alerte envoyée.")
        return

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to_addr

    context = ssl.create_default_context()
    with smtplib.SMTP(host, port, timeout=20) as server:
        server.starttls(context=context)
        server.login(user, pwd)
        server.sendmail(user, [to_addr], msg.as_string())
    log("Email d’alerte envoyé.")

def parse_availability(html: str) -> Tuple[bool, str]:
    soup = BeautifulSoup(html, "html.parser")
    raw_text = soup.get_text(separator=" ", strip=True)
    text = normalize_text(raw_text)

    neg_full_norm = normalize_text(NEGATIVE_PHRASE_FULL)
    if neg_full_norm in text:
        return False, "Message négatif exact détecté"

    # Simplification demandée: si la phrase exacte n'est pas présente,
    # on considère qu'il y a potentiellement des créneaux.
    return True, "Phrase négative absente"

async def fetch_html_with_playwright(url: str, timeout_ms: int = 30000) -> str:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            locale="fr-FR",
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )
        page = await ctx.new_page()
        try:
            await page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            await page.wait_for_load_state("networkidle")
            await page.wait_for_timeout(1500)
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await page.wait_for_timeout(800)
            await page.evaluate("window.scrollTo(0, 0)")
            await page.wait_for_timeout(300)
            html = await page.content()
            return html
        finally:
            await ctx.close()
            await browser.close()

async def main():
    load_dotenv()
    log("Surveillance démarrée")
    last_signature = load_state()

    while True:
        try:
            html = await fetch_html_with_playwright(URL)
            has_slots, detail = parse_availability(html)

            signature = str(hash(html[:50000]))

            if has_slots:
                if signature != last_signature:
                    subject = "Alerte rendez-vous conciliateur Paris"
                    body = (
                        "Des indices de créneaux viennent d’apparaître.\n"
                        f"Détails: {detail}\n"
                        f"Page: {URL}\n\n"
                        "Ouvre la page et réserve sans attendre."
                    )
                    send_email(subject, body)
                    save_state(signature)
                    last_signature = signature
                else:
                    log("Disponibilités déjà signalées. Pas de nouvel email.")
            else:
                log("Aucune disponibilité détectée.")
        except Exception as e:
            log(f"Erreur: {e}")

        sleep_for = BASE_POLL_SEC + random.randint(-JITTER_SEC, JITTER_SEC)
        if sleep_for < 20:
            sleep_for = 20
        await asyncio.sleep(sleep_for)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log("Arrêt demandé")
        sys.exit(0)
