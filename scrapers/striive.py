"""Striive Supplier - job requests.

Striive is herbrand naar "Vertage": supplier.striive.com/auth.striive.com
redirecten inmiddels naar partner.vertage.com/auth.vertage.com (zelfde
Auth0-app, zelfde inlogformulier, alleen de domeinnamen zijn gewijzigd).
We gaan daarom direct naar het nieuwe domein in plaats van op de oude
redirect te vertrouwen.

Login via Auth0 (auth.vertage.com); de app wisselt dit in voor
sessiecookies op partner.vertage.com. Die cookies hergebruiken we voor
gewone requests-calls naar de API, in plaats van de hele (virtueel
gescrollde) lijst via Playwright te scrapen.

De inlogpagina toont soms een cookiebanner die we eerst wegklikken voor
de zekerheid. We controleren niet op een specifieke cookie-naam: de
API-call hieronder gebruikt toch alle cookies van het domein, dus een
harde naam-check zou een vals-negatieve fout kunnen geven; een echte
auth-fout komt naar boven via de HTTP-statuscode van de job-requests-call
zelf.
"""

import os
import requests
from playwright.sync_api import sync_playwright

BRON = "striive"

APP = "https://partner.vertage.com"
API = f"{APP}/api/v2/job-requests"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


# ---------------------------------------------------------------- login

def _accept_cookies(page):
    for txt in ["Alle cookies accepteren", "Alles accepteren", "Accepteren"]:
        try:
            b = page.query_selector(f"button:has-text('{txt}')")
            if b and b.is_visible():
                b.click()
                page.wait_for_timeout(600)
                return
        except Exception:
            pass


def _sessie_cookies():
    email = os.environ.get("STRIIVE_EMAIL")
    wachtwoord = os.environ.get("STRIIVE_WACHTWOORD")
    if not email or not wachtwoord:
        raise RuntimeError("STRIIVE_EMAIL of STRIIVE_WACHTWOORD ontbreekt")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        page.goto(f"{APP}/dashboard", timeout=60000)
        page.wait_for_selector("#username", timeout=30000)
        _accept_cookies(page)
        page.fill("#username", email)

        # Auth0 heeft een onzichtbare lok-submitknop vóór de echte; klik de primaire
        primair = "button[data-action-button-primary='true']"

        # Sinds de overgang naar Vertage is het inloggen in twee stappen
        # (/u/login/identifier: alleen e-mail + "Doorgaan"; daarna pas
        # /u/login/password). Op de eerste pagina staat wel een verborgen
        # wachtwoordveld, maar zonder id="password". Daarom: alleen eerst op
        # "Doorgaan" klikken als er nog geen zichtbaar wachtwoordveld is, zodat de
        # oude variant met alles op één pagina ook blijft werken.
        wachtwoordveld = page.locator("input[type='password']:visible").first
        try:
            if not wachtwoordveld.is_visible():
                page.click(primair)
            wachtwoordveld.wait_for(state="visible", timeout=30000)
        except Exception:
            try:
                page.screenshot(path="debug_striive_login.png", full_page=True)
            except Exception:
                pass
            raise RuntimeError(
                f"Wachtwoordveld niet gevonden op {page.url.split('?')[0]}, zie debug_striive_login.png")
        wachtwoordveld.fill(wachtwoord)
        page.click(primair)

        try:
            page.wait_for_url(f"{APP}/**", timeout=30000)
        except Exception:
            pass
        page.wait_for_timeout(5000)

        cookies = {
            c["name"]: c["value"]
            for c in context.cookies()
            if "vertage.com" in c["domain"]
        }

        if not cookies:
            try:
                page.screenshot(path="debug_striive_login.png", full_page=True)
            except Exception:
                pass

        browser.close()

    if not cookies:
        raise RuntimeError("Geen cookies ontvangen na login, zie debug_striive_login.png")

    return cookies


# ---------------------------------------------------------------- ophalen

def _job_requests(cookies, blok=1000):
    """Haalt alle job requests op, gepagineerd."""
    sessie = requests.Session()
    sessie.cookies.update(cookies)
    headers = {"user-agent": UA, "accept": "application/json"}

    alles = []
    pagina_nr = 0

    while True:
        r = sessie.get(
            API,
            params={
                "page": pagina_nr,
                "size": blok,
                "maxRadius": 50,
                "sortBy": "",
                "sortOrder": "ASCENDING",
                "clientNames": "",
                "professionalTypes": "",
                "remoteAllowed": "",
                "locations": "",
                "skills": "",
            },
            headers=headers,
            timeout=30,
        )
        r.raise_for_status()

        pagina = r.json()
        if not pagina:
            break

        alles.extend(pagina)
        if len(pagina) < blok:
            break
        pagina_nr += 1

    return alles


# ---------------------------------------------------------------- normaliseren

def _uit_job(j):
    jid = j.get("id")
    client = j.get("client") or {}
    return {
        "tender_id": str(jid),
        "nummer": j.get("referenceCode"),
        "titel": j.get("title"),
        "organisatie": client.get("name"),
        "status": j.get("state"),
        "deadline": j.get("closingDateOffer"),
        "publicatiedatum": j.get("publishedAt"),
        "locatie": j.get("locationName"),
        "url": f"{APP}/inbox/all/{jid}",
    }


# ---------------------------------------------------------------- publiek

def haal_op():
    """Wordt aangeroepen door run.py. Geeft een lijst dicts terug."""
    cookies = _sessie_cookies()
    print("  ingelogd")

    jobs = _job_requests(cookies)
    print(f"  {len(jobs)} opdrachten gevonden")

    return [_uit_job(j) for j in jobs]
