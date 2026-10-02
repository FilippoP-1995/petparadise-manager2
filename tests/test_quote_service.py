import copy
import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import app
import quote_service as qs


def pricelist():
    return qs.validate_pricelist(copy.deepcopy(qs.DEFAULT_PRICELIST))


def total(**kwargs):
    return qs.calculate_quote(pricelist(), **kwargs)["total"]


class CremationPriceTests(unittest.TestCase):
    def test_every_band_of_the_printed_price_list(self):
        expected = [
            ("1", 210), ("5", 210), ("5.1", 250), ("10", 250), ("10.1", 270), ("15", 270), ("15.1", 290), ("20", 290),
            ("20.1", 310), ("25", 310), ("25.1", 330), ("30", 330), ("30.1", 350), ("35", 350), ("35.1", 380),
            ("40", 380), ("40.1", 400), ("45", 400), ("45.1", 450), ("50", 450), ("50.1", 520), ("60", 520),
            ("60.1", 570), ("70", 570), ("70.1", 620), ("120", 620),
        ]
        for weight, price in expected:
            with self.subTest(weight=weight):
                self.assertEqual(total(weight=weight), Decimal(price))

    def test_upper_limit_of_each_band_is_included_and_comma_is_accepted(self):
        self.assertEqual(total(weight="10"), Decimal(250))
        self.assertEqual(total(weight="10,1"), Decimal(270))
        self.assertEqual(total(weight=" 12,5 "), Decimal(270))
        self.assertEqual(total(weight=22), Decimal(310))

    def test_only_the_cremation_line_without_pickup_or_delivery(self):
        result = qs.calculate_quote(pricelist(), weight="4")
        self.assertEqual([line["label"] for line in result["lines"]], ["Cremazione singola"])
        self.assertEqual(result["lines"][0]["detail"], "0-5 kg")

    def test_invalid_weights(self):
        for bad in ("", "abc", "0", "-3", "301", "nan", "inf"):
            with self.subTest(weight=bad):
                with self.assertRaises(qs.QuoteError):
                    qs.calculate_quote(pricelist(), weight=bad)


class PickupPriceTests(unittest.TestCase):
    def test_full_pickup_table_inside_the_circondario(self):
        table = {
            "base": {"5": 50, "10": 50, "10.1": 60, "30": 60, "30.1": 70, "45": 70, "45.1": 80, "90": 80},
            "piano12": {"5": 60, "10": 60, "10.1": 70, "30": 70, "30.1": 80, "45": 80, "45.1": 90, "90": 90},
            "piano3": {"5": 70, "10": 70, "10.1": 80, "30": 80, "30.1": 90, "45": 90, "45.1": 100, "90": 100},
        }
        places = {"base": ("ambulatorio", "terra", "ascensore"), "piano12": ("piano12",), "piano3": ("piano3",)}
        for tariff, rows in table.items():
            for place in places[tariff]:
                for weight, price in rows.items():
                    with self.subTest(place=place, weight=weight):
                        result = qs.calculate_quote(pricelist(), weight=weight, pickup_place=place, pickup_comune="Livorno")
                        pickup = [line for line in result["lines"] if line["label"].startswith("Ritiro")]
                        self.assertEqual([line["amount"] for line in pickup], [Decimal(price)])

    def test_examples_agreed_with_the_owner(self):
        self.assertEqual(total(weight="22", pickup_place="piano12", pickup_comune="Livorno"), Decimal(380))
        self.assertEqual(total(weight="22", pickup_place="piano12", pickup_comune=qs.OTHER_COMUNE), Decimal(390))
        self.assertEqual(total(weight="4", pickup_place="ambulatorio", pickup_comune="Empoli"), Decimal(260))

    def test_outside_the_circondario_adds_ten_euros_as_its_own_line(self):
        result = qs.calculate_quote(pricelist(), weight="8", pickup_place="terra", pickup_comune=qs.OTHER_COMUNE)
        labels = [line["label"] for line in result["lines"]]
        self.assertIn("Ritiro fuori circondario", labels)
        self.assertEqual(result["total"], Decimal(250 + 50 + 10))

    def test_circondario_membership(self):
        pl = pricelist()
        livorno_only = ["Livorno", "livorno", " LIVORNO "]
        for name in livorno_only:
            self.assertEqual(qs.comune_zone(pl, name), "Livorno")
        empolese = ["Capraia e Limite", "Castelfiorentino", "Cerreto Guidi", "Certaldo", "Empoli", "Fucecchio",
                    "Gambassi Terme", "Montaione", "Montelupo Fiorentino", "Montespertoli", "Vinci"]
        for name in empolese:
            self.assertEqual(qs.comune_zone(pl, name), "Circondario Empolese Valdelsa", name)
            self.assertEqual(total(weight="3", pickup_place="terra", pickup_comune=name), Decimal(260), name)
        for outside in ("Pisa", "Firenze", "Collesalvetti", "Rosignano Marittimo"):
            self.assertIsNone(qs.comune_zone(pl, outside))

    def test_pickup_needs_valid_place_and_comune(self):
        with self.assertRaises(qs.QuoteError):
            qs.calculate_quote(pricelist(), weight="5", pickup_place="tetto", pickup_comune="Livorno")
        for bad in ("", "Atlantide"):
            with self.subTest(comune=bad):
                with self.assertRaises(qs.QuoteError):
                    qs.calculate_quote(pricelist(), weight="5", pickup_place="terra", pickup_comune=bad)

    def test_client_bringing_the_animal_pays_no_pickup(self):
        result = qs.calculate_quote(pricelist(), weight="12", pickup_place="", pickup_comune="Livorno")
        self.assertEqual(len(result["lines"]), 1)


class DeliveryPriceTests(unittest.TestCase):
    def test_delivery_inside_and_outside(self):
        self.assertEqual(total(weight="5", delivery=True, delivery_comune="Livorno"), Decimal(210 + 40))
        self.assertEqual(total(weight="5", delivery=True, delivery_comune="Vinci"), Decimal(210 + 40))
        self.assertEqual(total(weight="5", delivery=True, delivery_comune=qs.OTHER_COMUNE), Decimal(210 + 50))

    def test_delivery_defaults_to_the_pickup_comune(self):
        for default in ("", qs.SAME_COMUNE):
            self.assertEqual(total(weight="5", pickup_place="terra", pickup_comune="Livorno", delivery=True,
                                   delivery_comune=default), Decimal(210 + 50 + 40))
            self.assertEqual(total(weight="5", pickup_place="terra", pickup_comune=qs.OTHER_COMUNE, delivery=True,
                                   delivery_comune=default), Decimal(210 + 50 + 10 + 50))

    def test_delivery_can_differ_from_pickup(self):
        self.assertEqual(total(weight="5", pickup_place="terra", pickup_comune="Livorno", delivery=True,
                               delivery_comune=qs.OTHER_COMUNE), Decimal(210 + 50 + 50))

    def test_delivery_without_pickup_needs_a_comune(self):
        for bad in ("", qs.SAME_COMUNE):
            with self.assertRaises(qs.QuoteError):
                qs.calculate_quote(pricelist(), weight="5", delivery=True, delivery_comune=bad)

    def test_delivery_is_flat_it_does_not_depend_on_weight(self):
        light = total(weight="3", delivery=True, delivery_comune="Livorno") - total(weight="3")
        heavy = total(weight="80", delivery=True, delivery_comune="Livorno") - total(weight="80")
        self.assertEqual((light, heavy), (Decimal(40), Decimal(40)))


class SupplementTests(unittest.TestCase):
    def quote(self, when, **kw):
        base = dict(weight="5", pickup_place="terra", pickup_comune="Livorno", when=when)
        base.update(kw)
        return qs.calculate_quote(pricelist(), **base)

    def labels(self, result):
        return [line["label"] for line in result["lines"]]

    def test_evening_and_night_windows(self):
        cases = [
            (datetime(2026, 10, 5, 12, 0), None), (datetime(2026, 10, 5, 18, 59), None), (datetime(2026, 10, 5, 19, 0), 50),
            (datetime(2026, 10, 5, 20, 59), 50), (datetime(2026, 10, 5, 21, 0), 120), (datetime(2026, 10, 5, 23, 59), 120),
            (datetime(2026, 10, 6, 0, 0), 120), (datetime(2026, 10, 6, 6, 59), 120), (datetime(2026, 10, 6, 7, 0), None),
        ]
        for when, extra in cases:  # lunedi' 5 e martedi' 6 ottobre 2026: giorni feriali
            with self.subTest(when=when):
                result = self.quote(when)
                self.assertEqual(result["total"], Decimal(210 + 50 + (extra or 0)))

    def test_sundays_and_national_holidays_are_festivo(self):
        for day in (date(2026, 10, 4), date(2026, 1, 1), date(2026, 1, 6), date(2026, 4, 5), date(2026, 4, 6),
                    date(2026, 4, 25), date(2026, 5, 1), date(2026, 6, 2), date(2026, 8, 15), date(2026, 11, 1),
                    date(2026, 12, 8), date(2026, 12, 25), date(2026, 12, 26), date(2027, 3, 29)):
            with self.subTest(day=day):
                self.assertTrue(qs.is_festivo(day))
                self.assertIn("Servizio festivo", self.labels(self.quote(datetime.combine(day, datetime.min.time().replace(hour=10)))))
        for day in (date(2026, 10, 5), date(2026, 12, 24), date(2026, 5, 22), date(2026, 11, 30)):  # patroni locali: non nazionali
            self.assertFalse(qs.is_festivo(day))

    def test_easter_dates(self):
        self.assertEqual(qs.easter_sunday(2025), date(2025, 4, 20))
        self.assertEqual(qs.easter_sunday(2026), date(2026, 4, 5))
        self.assertEqual(qs.easter_sunday(2027), date(2027, 3, 28))
        self.assertEqual(qs.easter_sunday(2028), date(2028, 4, 16))

    def test_festivo_and_night_add_up(self):
        result = self.quote(datetime(2026, 10, 4, 22, 30))  # domenica notte
        self.assertEqual(self.labels(result)[-2:], ["Servizio festivo", "Servizio notturno"])
        self.assertEqual(result["total"], Decimal(210 + 50 + 80 + 120))

    def test_date_without_time_only_checks_festivo_and_hints(self):
        result = self.quote(datetime(2026, 10, 5, 12, 0), time_known=False)
        self.assertEqual(result["total"], Decimal(260))
        self.assertEqual(len(result["hints"]), 1)
        sunday = self.quote(datetime(2026, 10, 4, 12, 0), time_known=False)
        self.assertEqual(sunday["total"], Decimal(260 + 80))

    def test_no_supplement_without_pickup_or_delivery(self):
        result = qs.calculate_quote(pricelist(), weight="5", when=datetime(2026, 10, 4, 22, 30))
        self.assertEqual(result["total"], Decimal(210))
        with_delivery = qs.calculate_quote(pricelist(), weight="5", delivery=True, delivery_comune="Livorno",
                                           when=datetime(2026, 10, 4, 22, 30))
        self.assertEqual(with_delivery["total"], Decimal(210 + 40 + 80 + 120))


class UrnAndFormattingTests(unittest.TestCase):
    def test_urn_is_a_note_never_part_of_the_total(self):
        result = qs.calculate_quote(pricelist(), weight="5")
        self.assertIn("non è inclusa", result["urn_note"])
        self.assertIn("€ 20,00", result["urn_note"])
        self.assertEqual(result["total"], Decimal(210))
        self.assertFalse(any("rna" in line["label"] for line in result["lines"]))

    def test_money_and_weight_formatting(self):
        self.assertEqual(qs.fmt_eur(Decimal("1234.5")), "€ 1.234,50")
        self.assertEqual(qs.fmt_eur(380), "€ 380,00")
        self.assertEqual(qs.fmt_kg(Decimal("10.1")), "10,1")
        self.assertEqual(qs.fmt_kg(Decimal("30")), "30")

    def test_band_labels(self):
        pl = pricelist()
        self.assertEqual([qs.cremation_band_label(pl, i) for i in (0, 1, 2, 11, 12)],
                         ["0-5 kg", "5,1-10 kg", "10,1-15 kg", "60,1-70 kg", "oltre 70 kg"])
        self.assertEqual([qs.pickup_band_label(pl, i) for i in range(4)],
                         ["0-10 kg", "10,1-30 kg", "30,1-45 kg", "oltre 45 kg"])


class PricelistValidationTests(unittest.TestCase):
    def test_default_pricelist_is_valid_and_matches_the_printed_one(self):
        pl = pricelist()
        self.assertEqual([row["price"] for row in pl["cremation"]], [210, 250, 270, 290, 310, 330, 350, 380, 400, 450, 520, 570, 620])
        self.assertEqual(pl["pickup"]["bands"], [10, 30, 45])
        self.assertEqual(pl["pickup"]["outside_surcharge"], 10)
        self.assertEqual(pl["delivery"], {"inside": 40, "outside": 50})
        self.assertEqual(pl["supplements"], {"festivo": 80, "serale": 50, "notturno": 120})
        self.assertEqual(pl["urn"]["from_price"], 20)

    def test_invalid_pricelists_are_rejected(self):
        def mutate(fn):
            data = copy.deepcopy(qs.DEFAULT_PRICELIST)
            fn(data)
            return data
        bad = [
            mutate(lambda d: d["cremation"].__setitem__(1, {"max_kg": 3, "price": 250})),            # non crescente
            mutate(lambda d: d["cremation"][0].__setitem__("price", -5)),
            mutate(lambda d: d["cremation"][0].__setitem__("price", "abc")),
            mutate(lambda d: d["cremation"][-1].__setitem__("max_kg", 99)),
            mutate(lambda d: d["pickup"]["prices"]["base"].pop()),
            mutate(lambda d: d["pickup"].__setitem__("bands", [30, 10, 45])),
            mutate(lambda d: d["delivery"].__setitem__("inside", "")),
            mutate(lambda d: d["supplements"].pop("festivo")),
            mutate(lambda d: d["circondari"].__setitem__(0, {"name": "Livorno", "comuni": []})),
            mutate(lambda d: d["circondari"][1]["comuni"].append("Livorno")),                          # comune doppio
            {}, None, "x",
        ]
        for data in bad:
            with self.subTest(data=str(data)[:60]):
                with self.assertRaises(qs.QuoteError):
                    qs.validate_pricelist(data)


class PricelistStorageAndAdminTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.old = (app.DATA, app.DB_PATH, app.DDT_DIR)
        app.DATA = Path(self.temp.name)
        app.DB_PATH = app.DATA / "test.db"
        app.DDT_DIR = app.DATA / "ddt"
        app.init_db()
        self.handler = object.__new__(app.App)
        self.handler.headers = {}
        with app.db() as c:
            self.admin = c.execute("SELECT * FROM users WHERE username='admin'").fetchone()
            self.serena = c.execute("SELECT * FROM users WHERE username='serena'").fetchone()

    def tearDown(self):
        app.DATA, app.DB_PATH, app.DDT_DIR = self.old
        self.temp.cleanup()

    def default_form(self):
        pl = pricelist()
        form = {}
        for i, row in enumerate(pl["cremation"]):
            if row["max_kg"] is not None:
                form[f"cr_max_{i}"] = str(row["max_kg"])
            form[f"cr_price_{i}"] = str(row["price"])
        for i, band in enumerate(pl["pickup"]["bands"]):
            form[f"pk_band_{i}"] = str(band)
        for tariff, row in pl["pickup"]["prices"].items():
            for j, price in enumerate(row):
                form[f"pk_{tariff}_{j}"] = str(price)
        form.update({"pk_outside": "10", "dl_inside": "40", "dl_outside": "50", "sp_festivo": "80", "sp_serale": "50",
                     "sp_notturno": "120", "urn_from": "20"})
        for i, item in enumerate(pl["circondari"]):
            form[f"circ_{i}"] = "\n".join(item["comuni"])
        return form

    def test_without_saved_list_the_default_is_used_and_corrupt_data_falls_back(self):
        with app.db() as c:
            self.assertEqual(qs.get_pricelist(c), pricelist())
            c.execute("INSERT INTO settings(key,value) VALUES(?,?)", (qs.SETTINGS_KEY, "{non json"))
            self.assertEqual(qs.get_pricelist(c), pricelist())
            c.execute("UPDATE settings SET value=? WHERE key=?", ('{"pricelist": {"cremation": []}}', qs.SETTINGS_KEY))
            self.assertEqual(qs.get_pricelist(c), pricelist())

    def test_saved_list_is_used_by_the_calculation_and_survives_reload(self):
        form = self.default_form()
        form.update({"cr_price_0": "215,50", "dl_inside": "45", "circ_0": "Livorno\nCollesalvetti"})
        self.handler.form = lambda: form
        redirects = []
        self.handler.redirect = redirects.append
        self.handler.portal_quote_pricelist_save(self.admin)
        self.assertEqual(redirects, ["/portale-partner/listino?ok=1"])
        with app.db() as c:
            saved = qs.get_pricelist(c)
            meta = qs.get_pricelist_meta(c)
        self.assertEqual(saved["cremation"][0]["price"], 215.5)
        self.assertEqual(saved["delivery"]["inside"], 45)
        self.assertEqual(qs.comune_zone(saved, "Collesalvetti"), "Livorno")
        self.assertEqual(meta["updated_by"], self.admin["display_name"])
        result = qs.calculate_quote(saved, weight="3", delivery=True, delivery_comune="Collesalvetti")
        self.assertEqual(result["total"], Decimal("215.50") + 45)

    def test_invalid_form_is_rejected_and_keeps_what_was_typed(self):
        form = self.default_form()
        form.update({"cr_price_3": "abc", "cr_price_0": "999"})
        self.handler.form = lambda: form
        pages, redirects = [], []
        self.handler.send_html = lambda content, *a: pages.append(content)
        self.handler.redirect = redirects.append
        self.handler.path = "/portale-partner/listino"
        self.handler.portal_quote_pricelist_save(self.admin)
        self.assertEqual(redirects, [])
        self.assertIn("Cremazione, prezzo: numero non valido", pages[-1])
        self.assertIn('value="999"', pages[-1])  # il resto di quanto scritto non si perde
        with app.db() as c:
            self.assertEqual(qs.get_pricelist(c)["cremation"][0]["price"], 210)

    def test_non_ascending_weights_and_duplicate_comuni_are_rejected(self):
        for change, fragment in (({"cr_max_2": "4"}, "crescenti"), ({"pk_band_1": "5"}, "crescenti"),
                                 ({"circ_1": "Empoli\nEmpoli"}, "ripetuto"), ({"circ_0": "  \n "}, "almeno un comune")):
            with self.subTest(change=change):
                form = self.default_form()
                form.update(change)
                self.handler.form = lambda form=form: form
                pages = []
                self.handler.send_html = lambda content, *a: pages.append(content)
                self.handler.redirect = lambda url: pages.append("REDIRECT")
                self.handler.path = "/portale-partner/listino"
                self.handler.portal_quote_pricelist_save(self.admin)
                self.assertNotIn("REDIRECT", pages)
                self.assertIn(fragment, pages[-1])

    def test_reset_restores_the_printed_prices(self):
        form = self.default_form()
        form["cr_price_0"] = "300"
        self.handler.form = lambda: form
        self.handler.redirect = lambda url: None
        self.handler.portal_quote_pricelist_save(self.admin)
        redirects = []
        self.handler.redirect = redirects.append
        self.handler.form = lambda: {"reset": "1"}
        self.handler.portal_quote_pricelist_save(self.admin)
        self.assertEqual(redirects, ["/portale-partner/listino?ok=2"])
        with app.db() as c:
            self.assertEqual(qs.get_pricelist(c), pricelist())

    def test_only_admins_can_see_or_change_the_price_list(self):
        errors, redirects = [], []
        self.handler.send_error = lambda code, *a: errors.append(code)
        self.handler.redirect = redirects.append
        self.handler.send_html = lambda *a: errors.append("HTML")
        self.handler.path = "/portale-partner/listino"
        self.handler.form = lambda: self.default_form()
        self.handler.portal_quote_pricelist_page(self.serena)
        self.handler.portal_quote_pricelist_save(self.serena)
        self.assertEqual(errors, [403, 403])
        self.assertEqual(redirects, [])

    def test_admin_page_shows_the_whole_list_and_is_linked_from_the_portal_admin_page(self):
        pages = []
        self.handler.send_html = lambda content, *a: pages.append(content)
        self.handler.path = "/portale-partner/listino"
        self.handler.portal_quote_pricelist_page(self.admin)
        html = pages[-1]
        for text in ("Cremazione singola", "Ritiro", "Riconsegna", "Servizio H24", "Urna", "Circondario",
                     'name="cr_price_12"', 'name="pk_piano3_3"', 'name="dl_outside"', 'name="sp_notturno"',
                     "Capraia e Limite", "Montespertoli", "Salva listino", "Ripristina prezzi di partenza"):
            self.assertIn(text, html)
        pages.clear()
        self.handler.path = "/portale-partner"
        self.handler.portal_partner_page(self.admin)
        self.assertIn('href="/portale-partner/listino"', pages[-1])


if __name__ == "__main__":
    unittest.main()
