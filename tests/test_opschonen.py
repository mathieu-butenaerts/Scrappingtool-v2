import os
import sqlite3
import tempfile
import unittest

import db


def _rij(bron, tid, laatst):
    return (bron, tid, "t", laatst, laatst)


class Opschonen(unittest.TestCase):
    def setUp(self):
        self.map = tempfile.mkdtemp()
        self.oud = db.DB
        db.DB = os.path.join(self.map, "t.db")
        conn = db.verbind()
        conn.execute("CREATE TABLE beschrijvingen (bron TEXT, tender_id TEXT, omschrijving TEXT, opgehaald_op TEXT)")
        conn.executemany(
            "INSERT INTO tenders (bron, tender_id, titel, eerst_gezien, laatst_gezien) VALUES (?,?,?,?,?)",
            [_rij("a", "nieuw", "2026-11-10T00:00:00+00:00"),
             _rij("a", "net-binnen", "2026-09-20T00:00:00+00:00"),   # 51 dagen terug: blijft
             _rij("a", "oud", "2026-09-01T00:00:00+00:00"),          # 70 dagen terug: weg
             _rij("b", "oud", "2026-07-16T00:00:00+00:00")])         # ander bron, zelfde id: weg
        conn.executemany("INSERT INTO beschrijvingen VALUES (?,?,?,?)",
                         [("a", "nieuw", "x", "n"), ("a", "oud", "x", "n"), ("b", "oud", "x", "n")])
        conn.executemany(
            "INSERT INTO verrijkingen (bron, tender_id, oms_hash, verrijkt_op, data, gesynct) VALUES (?,?,?,?,?,?)",
            [("a", "oud", "h", "n", "{}", 1),    # al gesynct: mag weg
             ("b", "oud", "h", "n", "{}", 0),    # nog niet gesynct: blijft
             ("a", "nieuw", "h", "n", "{}", 1)])
        conn.commit()
        conn.close()

    def tearDown(self):
        db.DB = self.oud

    def _tel(self, tabel):
        conn = sqlite3.connect(db.DB)
        n = conn.execute(f"SELECT count(*) FROM {tabel}").fetchone()[0]
        conn.close()
        return n

    def test_verwijdert_alleen_wat_te_oud_is(self):
        self.assertEqual(db.opschonen(60), 2)
        conn = sqlite3.connect(db.DB)
        over = {tuple(r) for r in conn.execute("SELECT bron, tender_id FROM tenders")}
        conn.close()
        self.assertEqual(over, {("a", "nieuw"), ("a", "net-binnen")})

    def test_omschrijving_en_verrijking_volgen_de_tender(self):
        db.opschonen(60)
        self.assertEqual(self._tel("beschrijvingen"), 1)
        # de ongesyncte verrijking van een verdwenen tender blijft bewaard
        self.assertEqual(self._tel("verrijkingen"), 2)

    def test_grens_loopt_vanaf_nieuwste_rij_niet_vanaf_nu(self):
        # alle rijen zijn "oud" t.o.v. vandaag, maar de nieuwste bepaalt de grens
        self.assertGreater(self._tel("tenders"), 0)
        db.opschonen(60)
        self.assertEqual(self._tel("tenders"), 2)

    def test_nul_dagen_zet_het_uit(self):
        self.assertEqual(db.opschonen(0), 0)
        self.assertEqual(self._tel("tenders"), 4)

    def test_lege_database(self):
        conn = sqlite3.connect(db.DB)
        conn.execute("DELETE FROM tenders")
        conn.commit()
        conn.close()
        self.assertEqual(db.opschonen(60), 0)

    def test_bestand_wordt_kleiner(self):
        conn = sqlite3.connect(db.DB)
        conn.executemany(
            "INSERT INTO tenders (bron, tender_id, titel, eerst_gezien, laatst_gezien) VALUES (?,?,?,?,?)",
            [("c", str(i), "x" * 200, "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00") for i in range(3000)])
        conn.commit()
        conn.close()
        voor = os.path.getsize(db.DB)
        db.opschonen(60)
        self.assertLess(os.path.getsize(db.DB), voor)


if __name__ == "__main__":
    unittest.main()
