"""Gedeelde database voor alle scrapers.

Elke scraper levert rijen aan met de velden uit VELDEN.
Deduplicatie gebeurt op (bron, tender_id).
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import datums

DB = "tenders.db"

# Het contract: dit zijn de velden die elke scraper moet aanleveren.
VELDEN = [
    "bron",
    "tender_id",
    "nummer",
    "titel",
    "organisatie",
    "status",
    "deadline",
    "publicatiedatum",
    "locatie",
    "url",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS tenders (
    bron             TEXT NOT NULL,
    tender_id        TEXT NOT NULL,
    nummer           TEXT,
    titel            TEXT,
    organisatie      TEXT,
    status           TEXT,
    deadline         TEXT,
    publicatiedatum  TEXT,
    locatie          TEXT,
    url              TEXT,
    eerst_gezien     TEXT NOT NULL,
    laatst_gezien    TEXT NOT NULL,
    PRIMARY KEY (bron, tender_id)
);

CREATE INDEX IF NOT EXISTS idx_publicatie ON tenders(publicatiedatum DESC);
CREATE INDEX IF NOT EXISTS idx_bron       ON tenders(bron);
CREATE INDEX IF NOT EXISTS idx_deadline   ON tenders(deadline);

CREATE TABLE IF NOT EXISTS verrijkingen (
    bron         TEXT NOT NULL,
    tender_id    TEXT NOT NULL,
    oms_hash     TEXT NOT NULL,
    model        TEXT,
    verrijkt_op  TEXT NOT NULL,
    data         TEXT NOT NULL,
    gesynct      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (bron, tender_id)
);
"""


def verbind():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)

    # Migratie: bestaande databases hadden nog geen locatie-kolom.
    kolommen = {r["name"] for r in conn.execute("PRAGMA table_info(tenders)")}
    if "locatie" not in kolommen:
        conn.execute("ALTER TABLE tenders ADD COLUMN locatie TEXT")

    return conn


def opslaan(rijen):
    """Slaat rijen op. Bestaande tenders worden bijgewerkt, niet gedupliceerd.

    Geeft terug: (aantal_nieuw, totaal_in_database)
    """
    conn = verbind()
    nu = datetime.now(timezone.utc).isoformat(timespec="seconds")

    voor = conn.execute("SELECT count(*) FROM tenders").fetchone()[0]

    for r in rijen:
        waarden = [r.get(v) for v in VELDEN]
        conn.execute(
            """
            INSERT INTO tenders
                (bron, tender_id, nummer, titel, organisatie,
                 status, deadline, publicatiedatum, locatie, url,
                 eerst_gezien, laatst_gezien)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT (bron, tender_id) DO UPDATE SET
                -- een leeg veld in deze run wist geen eerder gevonden waarde
                -- (bv. freelance.nl valt bij een captcha terug op publieke data
                -- zonder organisatie en deadline)
                nummer          = COALESCE(excluded.nummer, tenders.nummer),
                titel           = COALESCE(excluded.titel, tenders.titel),
                organisatie     = COALESCE(excluded.organisatie, tenders.organisatie),
                status          = COALESCE(excluded.status, tenders.status),
                deadline        = COALESCE(excluded.deadline, tenders.deadline),
                publicatiedatum = COALESCE(tenders.publicatiedatum, excluded.publicatiedatum),
                locatie         = COALESCE(excluded.locatie, tenders.locatie),
                url             = COALESCE(excluded.url, tenders.url),
                laatst_gezien   = excluded.laatst_gezien
            """,
            waarden + [nu, nu],
        )

    conn.commit()
    na = conn.execute("SELECT count(*) FROM tenders").fetchone()[0]
    conn.close()

    return na - voor, na


AANVULBAAR = ["nummer", "titel", "organisatie", "deadline", "publicatiedatum", "locatie", "url"]


def vul_aan_uit_db(rijen):
    """Vult lege velden van de rijen uit deze run aan met wat er al bewaard is,
    zodat ook Supabase (dat deze rijen krijgt) geen waarden kwijtraakt.
    Geeft het aantal aangevulde velden terug."""
    conn = verbind()
    aangevuld = 0
    for r in rijen:
        if not any(r.get(v) in (None, "") for v in AANVULBAAR):
            continue
        oud = conn.execute("SELECT * FROM tenders WHERE bron = ? AND tender_id = ?",
                           (r.get("bron"), str(r.get("tender_id")))).fetchone()
        if not oud:
            continue
        for v in AANVULBAAR:
            if r.get(v) in (None, "") and oud[v] not in (None, ""):
                r[v] = oud[v]
                aangevuld += 1
    conn.close()
    return aangevuld


def herstel(waarden):
    """Vult lege velden van bewaarde tenders met eerder bekende waarden
    (herstel.json, eenmalig opgebouwd uit oude snapshots in git). Idempotent:
    geeft alleen de rijen terug die echt veranderen, zodat run.py ze naar
    Supabase kan sturen."""
    conn = verbind()
    gewijzigd = []
    for h in waarden:
        oud = conn.execute("SELECT * FROM tenders WHERE bron = ? AND tender_id = ?",
                           (h["bron"], str(h["tender_id"]))).fetchone()
        if not oud:
            continue
        vul = {v: h[v] for v in AANVULBAAR if h.get(v) not in (None, "") and oud[v] in (None, "")}
        if not vul:
            continue
        conn.execute(f"UPDATE tenders SET {', '.join(f'{v} = ?' for v in vul)} WHERE bron = ? AND tender_id = ?",
                     [*vul.values(), h["bron"], str(h["tender_id"])])
        gewijzigd.append({**dict(oud), **vul})
    conn.commit()
    conn.close()
    return gewijzigd


def wis_onzin_deadlines():
    """'2900-01-01' en dergelijke zijn plaatshouders voor 'doorlopend': leeg maken."""
    conn = verbind()
    rijen = [dict(r) for r in conn.execute("SELECT * FROM tenders WHERE deadline >= '2100'")]
    if rijen:
        conn.execute("UPDATE tenders SET deadline = NULL WHERE deadline >= '2100'")
        conn.commit()
    conn.close()
    for r in rijen:
        r["deadline"] = None
    return rijen


def normaliseer_bestaande_datums():
    """Zet tekstdatums van eerder opgeslagen tenders om naar ISO (zie datums.py).

    Idempotent: alleen rijen die echt veranderen worden bijgewerkt en
    teruggegeven, zodat run.py ze ook naar Supabase kan sturen.
    """
    conn = verbind()
    kandidaten = conn.execute(
        "SELECT * FROM tenders WHERE deadline NOT GLOB '[0-9][0-9][0-9][0-9]-*' "
        "OR publicatiedatum NOT GLOB '[0-9][0-9][0-9][0-9]-*'"
    ).fetchall()
    gewijzigd = []
    for r in kandidaten:
        rij = datums.normaliseer_rij(dict(r))
        if rij["deadline"] != r["deadline"] or rij["publicatiedatum"] != r["publicatiedatum"]:
            conn.execute(
                "UPDATE tenders SET deadline = ?, publicatiedatum = ? WHERE bron = ? AND tender_id = ?",
                (rij["deadline"], rij["publicatiedatum"], rij["bron"], rij["tender_id"]),
            )
            gewijzigd.append(rij)
    conn.commit()
    conn.close()
    return gewijzigd


def deadlines_uit_omschrijving(haal_deadline):
    """Vult een lege deadline van eerder opgeslagen tenders aan uit hun
    omschrijving (zie deadlines.py). Idempotent; geeft de gewijzigde rijen
    terug zodat run.py ze ook naar Supabase kan sturen."""
    conn = verbind()
    try:
        kandidaten = conn.execute(
            "SELECT t.*, b.omschrijving FROM tenders t "
            "JOIN beschrijvingen b USING (bron, tender_id) "
            "WHERE (t.deadline IS NULL OR t.deadline = '') AND b.omschrijving IS NOT NULL"
        ).fetchall()
    except sqlite3.OperationalError:
        kandidaten = []  # tabel beschrijvingen bestaat nog niet
    gewijzigd = []
    for r in kandidaten:
        deadline = haal_deadline(r["omschrijving"], r["publicatiedatum"] or r["eerst_gezien"])
        if not deadline:
            continue
        conn.execute("UPDATE tenders SET deadline = ? WHERE bron = ? AND tender_id = ?",
                     (deadline, r["bron"], r["tender_id"]))
        rij = {k: r[k] for k in r.keys() if k != "omschrijving"}
        rij["deadline"] = deadline
        gewijzigd.append(rij)
    conn.commit()
    conn.close()
    return gewijzigd


def te_verrijken(maak_hash, limiet):
    """Niet-verlopen tenders met een omschrijving die nog niet (of met een oudere
    titel/omschrijving) verrijkt zijn, nieuwste eerst."""
    conn = verbind()
    vandaag = datetime.now(timezone.utc).date().isoformat()
    try:
        rijen = conn.execute(
            """
            SELECT t.bron, t.tender_id, t.titel, t.organisatie, t.locatie,
                   b.omschrijving, v.oms_hash AS oude_hash
            FROM tenders t
            JOIN beschrijvingen b ON b.bron = t.bron AND b.tender_id = t.tender_id
            LEFT JOIN verrijkingen v ON v.bron = t.bron AND v.tender_id = t.tender_id
            WHERE b.omschrijving <> '' AND (t.deadline IS NULL OR t.deadline >= ?)
            ORDER BY t.eerst_gezien DESC
            """,
            (vandaag,),
        ).fetchall()
    except sqlite3.OperationalError:
        rijen = []  # beschrijvingen-tabel bestaat nog niet
    conn.close()
    uit = []
    for r in rijen:
        h = maak_hash(r["titel"], r["omschrijving"])
        if h != r["oude_hash"]:
            uit.append({**dict(r), "oms_hash": h})
            if len(uit) >= limiet:
                break
    return uit


def verrijkingen_opslaan(rijen):
    conn = verbind()
    nu = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for r in rijen:
        conn.execute(
            """
            INSERT INTO verrijkingen (bron, tender_id, oms_hash, model, verrijkt_op, data, gesynct)
            VALUES (?,?,?,?,?,?,0)
            ON CONFLICT (bron, tender_id) DO UPDATE SET
                oms_hash = excluded.oms_hash, model = excluded.model,
                verrijkt_op = excluded.verrijkt_op, data = excluded.data, gesynct = 0
            """,
            (r["bron"], str(r["tender_id"]), r["oms_hash"], r["model"], nu,
             json.dumps(r["data"], ensure_ascii=False)),
        )
    conn.commit()
    conn.close()


def verrijkingen(alleen_ongesynct=False):
    conn = verbind()
    sql = "SELECT * FROM verrijkingen" + (" WHERE gesynct = 0" if alleen_ongesynct else "")
    rijen = [{**dict(r), "data": json.loads(r["data"])} for r in conn.execute(sql)]
    conn.close()
    return rijen


def markeer_gesynct(rijen):
    conn = verbind()
    conn.executemany(
        "UPDATE verrijkingen SET gesynct = 1 WHERE bron = ? AND tender_id = ?",
        [(r["bron"], r["tender_id"]) for r in rijen],
    )
    conn.commit()
    conn.close()


def opschonen(dagen=60):
    """Gooit tenders weg die al `dagen` dagen niet meer op hun bron staan, samen met
    hun omschrijving en (al gesynchroniseerde) verrijking, en krimpt daarna het bestand.

    Zonder dit groeit tenders.db onbeperkt (~3 MB per dag). Supabase houdt alles,
    dit is alleen de werkdatabase. De grens loopt vanaf de nieuwste `laatst_gezien`
    in de database, niet vanaf nu: als de scrapers een tijd uitvallen verdwijnt er
    dus niets. Geeft het aantal verwijderde tenders terug. dagen <= 0 doet niets.
    """
    if not dagen or dagen <= 0:
        return 0
    conn = sqlite3.connect(DB, isolation_level=None)   # autocommit: VACUUM kan niet in een transactie
    try:
        nieuwste = conn.execute("SELECT max(laatst_gezien) FROM tenders").fetchone()[0]
        if not nieuwste:
            return 0
        grens = (datetime.fromisoformat(nieuwste) - timedelta(days=dagen)).isoformat(timespec="seconds")
        tabellen = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        weg = "SELECT 1 FROM tenders t WHERE t.bron = {0}.bron AND t.tender_id = {0}.tender_id"
        conn.execute("BEGIN")
        n = conn.execute("DELETE FROM tenders WHERE laatst_gezien < ?", (grens,)).rowcount
        if "beschrijvingen" in tabellen:
            conn.execute(f"DELETE FROM beschrijvingen WHERE NOT EXISTS ({weg.format('beschrijvingen')})")
        if "verrijkingen" in tabellen:
            # een verrijking die nog niet naar Supabase is gegaan blijft staan
            conn.execute(f"DELETE FROM verrijkingen WHERE gesynct = 1 AND NOT EXISTS ({weg.format('verrijkingen')})")
        conn.execute("COMMIT")
        if n:
            conn.execute("VACUUM")
        return n
    finally:
        conn.close()


def alle_rijen():
    conn = verbind()
    rijen = conn.execute(
        "SELECT * FROM tenders ORDER BY publicatiedatum DESC"
    ).fetchall()
    conn.close()
    return rijen


def nieuw_sinds(tijdstip):
    """Tenders die voor het eerst gezien zijn na het opgegeven tijdstip (ISO-string)."""
    conn = verbind()
    rijen = conn.execute(
        "SELECT * FROM tenders WHERE eerst_gezien > ? ORDER BY publicatiedatum DESC",
        (tijdstip,),
    ).fetchall()
    conn.close()
    return rijen


def per_bron():
    conn = verbind()
    rijen = conn.execute(
        "SELECT bron, count(*) AS aantal FROM tenders GROUP BY bron ORDER BY aantal DESC"
    ).fetchall()
    conn.close()
    return rijen
