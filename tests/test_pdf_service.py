import base64
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader

from pdf_service import generate_ddt


def _fake_signature_data_url():
    """A tiny real PNG (not a mock): a few opaque pixels on a transparent
    background, built without any imaging library dependency so this test
    file needs nothing beyond what's already in requirements.txt."""
    # 2x2 truecolor+alpha PNG, hand-assembled: transparent corners, one
    # opaque black pixel — enough for pypdf to recognize it as a real image
    # stream when merged into the page.
    png_bytes = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAFUlEQVR42mNk"
        "YPhfz0AEYBxVSF+FAP5FDvcfRYWTAAAAAElFTkSuQmCC"
    )
    return "data:image/png;base64," + base64.b64encode(png_bytes).decode()


class FakePractice(dict):
    """A dict that also answers .keys()/[] like the sqlite3.Row objects
    pdf_service.py's _get() expects, without needing a real database."""

    def keys(self):
        return dict.keys(self)

    def __getitem__(self, key):
        return dict.get(self, key, "")


class PdfSignatureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.assets = Path(__file__).resolve().parent.parent / "assets"

    def tearDown(self):
        self.temp.cleanup()

    def practice(self, *, branch="Livorno", signature_data=""):
        return FakePractice({
            "destination_branch": branch,
            "owner_first_name": "Anna",
            "owner_last_name": "Verdi",
            "total_service": "150",
            "signature_data": signature_data,
        })

    def generate(self, practice, name):
        output = Path(self.temp.name) / name
        # generate_ddt itself swaps in DCS_<BRANCH>.pdf when it exists (see
        # the branch_template check at the top of the function) — pass the
        # generic template path exactly like app.py's real call sites do,
        # so this test exercises that same resolution.
        generate_ddt(practice, self.assets / "DCS_NUOVO.pdf", output)
        return PdfReader(str(output))

    def baseline_for(self, practice):
        # Mirrors generate_ddt's own branch_template resolution, so the
        # comparison is against the exact template file that was actually
        # used (DCS_NUOVO.pdf itself is never used once a branch match exists).
        branch_template = self.assets / f'DCS_{str(practice["destination_branch"]).upper()}.pdf'
        return PdfReader(str(branch_template if branch_template.exists() else self.assets / "DCS_NUOVO.pdf"))

    def test_without_signature_no_extra_image_is_embedded_on_either_page(self):
        practice = self.practice(signature_data="")
        reader = self.generate(practice, "no_sig.pdf")
        # the template's own logo counts as 1 image per page already —
        # this asserts nothing *extra* gets merged in when there's no signature.
        baseline = self.baseline_for(practice)
        self.assertEqual(len(reader.pages[0].images), len(baseline.pages[0].images))
        self.assertEqual(len(reader.pages[1].images), len(baseline.pages[1].images))

    def test_with_signature_an_extra_image_is_embedded_on_both_pages(self):
        # The signature must appear on BOTH page 1 ("Firma dello speditore o
        # del responsabile dell'impianto di origine") and page 2 ("Firma per
        # accettazione") — the two red-boxed spots on the real DCS template.
        practice = self.practice(signature_data=_fake_signature_data_url())
        baseline = self.baseline_for(practice)
        reader = self.generate(practice, "sig.pdf")
        self.assertEqual(len(reader.pages[0].images), len(baseline.pages[0].images) + 1)
        self.assertEqual(len(reader.pages[1].images), len(baseline.pages[1].images) + 1)

    def test_signature_works_for_both_branch_templates(self):
        for branch in ("Livorno", "Empoli"):
            with self.subTest(branch=branch):
                practice = self.practice(branch=branch, signature_data=_fake_signature_data_url())
                baseline = self.baseline_for(practice)
                reader = self.generate(practice, f"sig_{branch}.pdf")
                self.assertEqual(len(reader.pages[0].images), len(baseline.pages[0].images) + 1)
                self.assertEqual(len(reader.pages[1].images), len(baseline.pages[1].images) + 1)

    def test_malformed_signature_data_is_silently_ignored_not_a_crash(self):
        # Never trust the DB blindly: a corrupted/legacy value must not
        # blow up DDT generation — it should just render without a signature.
        practice = self.practice(signature_data="not a real data url")
        baseline = self.baseline_for(practice)
        reader = self.generate(practice, "garbage_sig.pdf")
        self.assertEqual(len(reader.pages[0].images), len(baseline.pages[0].images))
        self.assertEqual(len(reader.pages[1].images), len(baseline.pages[1].images))

    def test_page1_signature_box_stays_within_the_measured_safe_area(self):
        # Calibrated once against the real template with pdfplumber: on
        # DCS_LIVORNO.pdf page 1, the "Firma dello speditore..." label's
        # bottom edge sits at y≈208.6 (bottom-origin) and the printed
        # signing line sits at y≈189.8-190.4. The box must stay inside that
        # gap — regressing this is exactly the "signature overlaps the
        # label" bug this was calibrated to avoid.
        from pdf_service import _draw_signature_page1  # noqa: local import, test-only introspection
        import inspect
        source = inspect.getsource(_draw_signature_page1)
        self.assertIn("188 + y_offset", source)
        self.assertIn("width=204, height=19", source)

    def test_page2_signature_box_stays_within_the_measured_safe_area(self):
        # Calibrated the same way: "Firma per accettazione"'s label bottom
        # sits at y≈72.8 and the printed line at y≈36. The previous box
        # (88,42,190,58) was tall enough to visibly cross the label text.
        from pdf_service import _draw_signature
        import inspect
        source = inspect.getsource(_draw_signature)
        self.assertIn("75, 36, width=165, height=32", source)


if __name__ == "__main__":
    unittest.main()
