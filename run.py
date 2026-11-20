"""Draait alle scrapers en schrijft het resultaat naar de gedeelde database.
Een scraper die crasht stopt de rest niet, maar de run eindigt wel in een fout
zodat je het in GitHub Actions ziet.
"""
import os
import csv
import html
import json
import re
import sys
import subprocess
import traceback
from datetime import datetime, timezone, timedelta
 
# Zoveel bronnen mogen falen zonder de hele run rood te maken. Login-scrapers
# (mercell, striive, magnit, stedin-vms, portofrotterdam, staffingms, heart)
# zijn af en toe flakey; pas bij brede uitval is er echt iets structureel mis.
MAX_MISLUKT = 3
 
import db
import datums
import deadlines
import verrijking
import beschrijvingen
import tarieven
import dubbels
import sheets_writer
import supabase_sync
from scrapers import (mercell, flextender, hero, striive, freelancenl, ns,
                      stedin, tenderned, inhuurdesk_regio, gelderland,
                      flexwestbrabant, magnit, stedin_vms, opdrachtoverheid,
                      harveynash, portofrotterdam, staffingms, heart,
                      mipublic, freep, quest4, publiekepartner,
                      inhuurdesk_alliander, ns_vms, alliander_vms)
# import sharepoint_writer


SCRAPERS = [
    mercell,
    flextender,
    hero,
    striive,
    freelancenl,
    ns,
    stedin,
    tenderned,
    inhuurdesk_regio,
    gelderland,
    flexwestbrabant,
    magnit,
    stedin_vms,
    opdrachtoverheid,
    harveynash,
    portofrotterdam,
    staffingms,
    heart,
    mipublic,
    freep,
    quest4,
    publiekepartner,
    inhuurdesk_alliander,
    ns_vms,
    alliander_vms,
]
 
CSV_ALLES = "tenders.csv"
CSV_NIEUW = "tenders_nieuw.csv"
 
 
def exporteer(rijen, bestand):
    if not rijen:
        print(f"  {bestand}: niets te schrijven")
        return
    kolommen = rijen[0].keys()
    with open(bestand, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=kolommen, delimiter=";")
        w.writeheader()
        w.writerows(dict(r) for r in rijen)
    print(f"  {bestand}: {len(rijen)} rijen")
 
 
def schoon_rij(r):
    """Kleine opschoning die voor alle bronnen geldt: witruimte en HTML-entiteiten
    in de titel, een plaatsnaam achteraan de titel die al in locatie staat
    ("Projectleider | Den Haag"), en plaatshouder-deadlines (2900-01-01)."""
    t = r.get("titel")
    if isinstance(t, str):
        t = re.sub(r"[\s\u00a0\u200b]+", " ", html.unescape(t)).strip()
        plaats = re.split(r" - | \(|,", r.get("locatie") or "")[0].strip()
        if plaats and len(t) > len(plaats) + 5:
            t = re.sub(r"\s+[|\-–]\s*" + re.escape(plaats) + r"$", "", t, flags=re.I)
        r["titel"] = t
    if (r.get("deadline") or "") >= "2100":
        r["deadline"] = None
    return r


def main():
    startmoment = datetime.now(timezone.utc) - timedelta(seconds=5)
    alles = []
    mislukt = []
    leeg = []
 
    for scraper in SCRAPERS:
        naam = scraper.BRON
        print(f"\n=== {naam} ===")
        try:
            rijen = scraper.haal_op()
            for r in rijen:
                r["bron"] = naam
                datums.normaliseer_rij(r)
                schoon_rij(r)
            print(f"  {len(rijen)} rijen opgehaald")
            if not rijen:
                leeg.append(naam)
            alles.extend(rijen)
        except Exception as e:
            print(f"  MISLUKT: {e}")
            traceback.print_exc()
            mislukt.append(naam)
    
    if not alles:
        print("\nNiets opgehaald, database niet aangepast.")
        sys.exit(1)

    # Bronnen zonder deadlineveld zetten 'm vaak wel in de omschrijving.
    aangevuld = deadlines.vul_aan(alles)
    if aangevuld:
        print(f"\n  {aangevuld} deadlines aangevuld uit de omschrijving")
 
    print(f"\n=== database ===")
    hersteld = []
    if os.path.exists("herstel.json"):
        # eenmalig opgebouwd uit oude snapshots; idempotent, vult alleen lege velden
        with open("herstel.json", encoding="utf-8") as f:
            hersteld = db.herstel(json.load(f))
        if hersteld:
            print(f"  {len(hersteld)} tenders hersteld uit eerdere snapshots (herstel.json)")
    aangevuld = db.vul_aan_uit_db(alles)
    if aangevuld:
        print(f"  {aangevuld} lege velden aangevuld met eerder bewaarde waarden")
    nieuw, totaal = db.opslaan(alles)
    print(f"  {nieuw} nieuw, {totaal} totaal")
    gecorrigeerd = db.normaliseer_bestaande_datums()
    if gecorrigeerd:
        print(f"  {len(gecorrigeerd)} oudere tekstdatums omgezet naar ISO")
    uit_tekst = db.deadlines_uit_omschrijving(deadlines.haal_deadline)
    if uit_tekst:
        print(f"  {len(uit_tekst)} oudere deadlines aangevuld uit de omschrijving")
    gecorrigeerd += uit_tekst
    onzin = db.wis_onzin_deadlines()
    if onzin:
        print(f"  {len(onzin)} plaatshouder-deadlines (2100+) leeggemaakt")
    gecorrigeerd += onzin + hersteld

    for r in db.per_bron():
        print(f"  {r['bron']}: {r['aantal']}")
 
    # Omschrijvingen apart bewaren (hoofdtabel blijft licht). Scrapers die een
    # 'omschrijving' meegeven voeden zo de latere AI-inschatting van de eindklant.
    print(f"\n=== omschrijvingen ===")
    met_oms = []
    try:
        met_oms = [r for r in alles if r.get("omschrijving")]
        opgeslagen = beschrijvingen.opslaan(met_oms)
        print(f"  {opgeslagen} omschrijvingen bewaard")
        for r in beschrijvingen.per_bron():
            print(f"  {r[0]}: {r[1]}")
    except Exception as e:
        print(f"  omschrijvingen MISLUKT: {e}")

    print(f"\n=== AI-verrijking ===")
    if not verrijking.beschikbaar():
        print("  ANTHROPIC_API_KEY ontbreekt; overgeslagen")
    else:
        try:
            kandidaten = db.te_verrijken(verrijking.maak_hash, verrijking.MAX_PER_RUN)
            resultaten, fouten = verrijking.verrijk_alle(kandidaten)
            db.verrijkingen_opslaan(resultaten)
            print(f"  {len(resultaten)} verrijkt, {fouten} mislukt "
                  f"(max {verrijking.MAX_PER_RUN} per run, model {verrijking.MODEL})")
            if kandidaten and not resultaten:
                mislukt.append("AI-verrijking")
        except Exception as e:
            print(f"  AI-verrijking MISLUKT: {type(e).__name__}: {e}")
            mislukt.append("AI-verrijking")
 
    # Supabase is de databron voor het live dashboard (real-time, met een
    # per-tender status die gebruikers zelf zetten). Raakt nooit de
    # status/status_gewijzigd_door/status_gewijzigd_op kolommen aan.
    print(f"\n=== supabase ===")
    try:
        # rijen van deze run, aangevuld/overschreven met wat de database daarna corrigeerde
        per_sleutel = {(r["bron"], str(r.get("tender_id"))): r for r in alles}
        for r in gecorrigeerd:
            k = (r["bron"], str(r["tender_id"]))
            per_sleutel[k] = {**per_sleutel.get(k, {}), **r}
        n_tenders = supabase_sync.sync_tenders(list(per_sleutel.values()))
        print(f"  {n_tenders} tenders gesynct")
        n_oms = supabase_sync.sync_beschrijvingen(met_oms)
        print(f"  {n_oms} omschrijvingen gesynct")
        nog_te_syncen = db.verrijkingen(alleen_ongesynct=True)
        if nog_te_syncen:
            n_ai = supabase_sync.sync_verrijkingen(nog_te_syncen)
            db.markeer_gesynct(nog_te_syncen)
            print(f"  {n_ai} verrijkingen gesynct")
    except Exception as e:
        print(f"  Supabase-sync MISLUKT: {e}")
        traceback.print_exc()
        mislukt.append("Supabase")

    # Tarieven uit de omschrijvingen (en anders de AI-verrijking), alleen ter
    # info in het dashboard. Telt niet mee als mislukte bron: de tabel bestaat
    # pas na supabase/notities_tarieven.sql.
    print(f"\n=== tarieven ===")
    try:
        gevonden = tarieven.verzamel(alles)
        print(f"  {sum(1 for r in gevonden if r['tarief'])} tarieven en "
              f"{sum(1 for r in gevonden if r['uren'])} keer uren gevonden")
        print("  kenmerken: " + ", ".join(f"{k} {sum(1 for r in gevonden if r.get(k))}" for k in tarieven.KENMERKEN))
        # dezelfde opdracht bij meerdere bronnen: niets weghalen, wel per regel 'ook op'
        try:
            per_sleutel = {(r["bron"], str(r["tender_id"])): r for r in gevonden}
            koppels = dubbels.bereken()
            for k, v in koppels.items():
                per_sleutel.setdefault(k, {"bron": k[0], "tender_id": k[1]}).update(v)
            gevonden = list(per_sleutel.values())
            dubbel = [v for v in koppels.values() if v["groep"]]
            print(f"  {len(dubbel)} opdrachten staan ook bij een andere bron "
                  f"({len({v['groep'] for v in dubbel})} groepen)")
        except Exception as e:
            print(f"  dubbels MISLUKT: {e}")
        if supabase_sync._configured():
            print(f"  {supabase_sync.sync_tarieven(gevonden)} rijen gesynct")
    except Exception as e:
        print(f"  tarieven MISLUKT: {e}")
 
    print(f"\n=== export ===")
    alle = db.alle_rijen()
    exporteer(alle, CSV_ALLES)
    exporteer(db.nieuw_sinds(startmoment.isoformat(timespec="seconds")), CSV_NIEUW)
 
    print(f"\n=== sharepoint ===")
    try:
        sheets_writer.sync(alle)
    except Exception as e:
        print(f"  SharePoint-sync MISLUKT: {e}")
        traceback.print_exc()
        mislukt.append("SharePoint")
 
    print(f"\n=== dashboard ===")
    subprocess.run(["python", "dashboard.py"], check=True)
 
    # Als allerlaatste: oude tenders uit de werkdatabase halen, zodat tenders.db niet
    # onbeperkt groeit (de Sheet/CSV/export van deze run zijn al gemaakt). Supabase
    # houdt de volledige historie. RETENTIE_DAGEN=0 zet het uit.
    print(f"\n=== opschonen ===")
    try:
        dagen = int(os.environ.get("RETENTIE_DAGEN") or 60)
        grootte_voor = os.path.getsize(db.DB)
        verwijderd = db.opschonen(dagen)
        print(f"  {verwijderd} tenders ouder dan {dagen} dagen verwijderd, "
              f"tenders.db {grootte_voor / 1e6:.0f} -> {os.path.getsize(db.DB) / 1e6:.0f} MB")
    except Exception as e:
        print(f"  opschonen MISLUKT (database blijft ongewijzigd): {e}")
        traceback.print_exc()

    if leeg:
        print(f"\nLET OP: {len(leeg)} bron(nen) leverden 0 rijen: {', '.join(leeg)}")
    if mislukt:
        melding = f"Mislukte bronnen ({len(mislukt)}): {', '.join(mislukt)}"
        print(f"\n{melding}")
        # zichtbaar maken in de GitHub-samenvatting, ook als de run groen blijft
        samenvatting = os.environ.get("GITHUB_STEP_SUMMARY")
        if samenvatting:
            try:
                with open(samenvatting, "a", encoding="utf-8") as f:
                    f.write(f"\n⚠️ {melding}\n")
            except Exception:
                pass
        # een paar flakey (login-)bronnen tolereren; alleen bij brede uitval falen
        if len(mislukt) > MAX_MISLUKT:
            print(f"  meer dan {MAX_MISLUKT} bronnen mislukt -> run faalt")
            sys.exit(1)
        print(f"  binnen tolerantie ({len(mislukt)}/{MAX_MISLUKT}); run blijft groen")
 
    print("\nKlaar.")
 
 
if __name__ == "__main__":
    main()
