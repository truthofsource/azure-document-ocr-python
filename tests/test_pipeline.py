import json
from pathlib import Path
import tempfile
import unittest
import uuid
from ocr_project.pipeline import (
    analyze,
    resume,
    validate_file,
    operation_url,
    read_pages,
)
from ocr_project.runtime import Ledger, Stopped, digest

BASE = "https://fictional-ocr.cognitiveservices.azure.com"
OP = (
    BASE
    + "/documentintelligence/documentModels/prebuilt-read/analyzeResults/"
    + str(uuid.uuid4())
    + "?api-version=2024-11-30"
)
DATA = b"%PDF-fictional transport fixture, not a renderable real report"


class OcrTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(Path(self.tmp.name) / "ledger.db")
        self.rid = str(uuid.uuid4())
        self.calls = []

    def tearDown(self):
        self.ledger.close()
        self.tmp.cleanup()

    def http(self, method, url, data, bearer):
        self.calls.append((method, url))
        if method == "POST":
            payload = json.loads(data)
            self.assertIn("base64Source", payload)
            return 202, {"Operation-Location": OP}, b""
        return (
            200,
            {},
            json.dumps(
                {
                    "status": "succeeded",
                    "analyzeResult": {
                        "pages": [
                            {"pageNumber": 1, "lines": [{"content": "Fictional text"}]},
                            {"pageNumber": 2},
                        ]
                    },
                }
            ).encode(),
        )

    def run_ocr(self, **kwargs):
        return analyze(
            DATA,
            [1, 2],
            self.ledger,
            self.rid,
            "actor",
            BASE,
            allowed=True,
            reviewed_sha256=digest(DATA),
            http=kwargs.get("http", self.http),
            get_token=kwargs.get("get_token", lambda: "fake-token"),
            wait=lambda _: None,
        )

    def test_page_mapping_and_blank_page_preserved(self):
        result = self.run_ocr()
        self.assertEqual(
            [{"page": 1, "text": "Fictional text"}, {"page": 2, "text": ""}],
            result["pages"],
        )
        self.assertEqual("completed", self.ledger.get(self.rid)["state"])
        self.assertEqual(1, len([c for c in self.calls if c[0] == "POST"]))

    def test_completed_resume_makes_no_http_request(self):
        first = self.run_ocr()
        self.assertEqual(
            first,
            resume(
                self.ledger,
                self.rid,
                BASE,
                bearer="fake",
                http=lambda *a: self.fail("No HTTP allowed"),
            ),
        )

    def test_no_permission_no_submission(self):
        with self.assertRaises(Stopped):
            analyze(
                DATA,
                [1],
                self.ledger,
                self.rid,
                "actor",
                BASE,
                allowed=False,
                http=lambda *a: self.fail("Must not send"),
            )

    def test_hash_mismatch_no_submission(self):
        with self.assertRaises(Stopped):
            analyze(
                DATA,
                [1],
                self.ledger,
                self.rid,
                "actor",
                BASE,
                allowed=True,
                reviewed_sha256="wrong",
                http=lambda *a: self.fail("Must not send"),
            )

    def test_token_failure_is_not_sent(self):
        def fail():
            raise Stopped("No Azure credential")

        with self.assertRaises(Stopped):
            self.run_ocr(get_token=fail)
        self.assertEqual("not_sent", self.ledger.get(self.rid)["state"])

    def test_timeout_is_unknown_no_paid_retry(self):
        def fail(*args):
            self.calls.append(args[0])
            raise TimeoutError()

        with self.assertRaises(TimeoutError):
            self.run_ocr(http=fail)
        self.assertEqual(["POST"], self.calls)
        self.assertEqual("unknown", self.ledger.get(self.rid)["state"])

    def test_persisted_receipt_resumes_only_get(self):
        def running(method, url, data, bearer):
            if method == "POST":
                return 202, {"Operation-Location": OP}, b""
            raise TimeoutError()

        with self.assertRaises(TimeoutError):
            self.run_ocr(http=running)
        self.calls.clear()
        result = resume(
            self.ledger,
            self.rid,
            BASE,
            bearer="fake",
            http=self.http,
            wait=lambda _: None,
        )
        self.assertEqual("GET", self.calls[0][0])
        self.assertEqual("completed", self.ledger.get(self.rid)["state"])
        self.assertEqual(2, len(result["pages"]))

    def test_foreign_operation_url_never_gets_token(self):
        for op in [
            OP.replace(BASE, "https://evil.test"),
            OP + "&extra=1",
            OP + "#fragment",
        ]:
            with self.assertRaises(Stopped):
                operation_url(BASE, op)

    def test_missing_or_duplicate_page_rejected(self):
        for pages in [[{"pageNumber": 1}], [{"pageNumber": 1}, {"pageNumber": 1}]]:
            with self.assertRaises(Stopped):
                read_pages({"analyzeResult": {"pages": pages}}, [1, 2])

    def test_invalid_type_page_or_overlarge_source(self):
        for data, pages in [
            (b"not jpg", [1]),
            (DATA, [True]),
            (DATA, [1, 1]),
            (b"\xff\xd8\xff", [2]),
            (DATA, [501]),
            (b"%PDF-" + b"x" * 10485760, [1]),
        ]:
            with self.assertRaises(Stopped):
                validate_file(data, pages)

    def test_pending_status_keeps_hold(self):
        self.ledger.reserve(self.rid, "actor", "hash", 1)
        self.ledger.receipt(
            self.rid, json.dumps({"url": OP, "pages": [1], "sha256": digest(DATA)})
        )
        with self.assertRaises(Stopped):
            resume(
                self.ledger,
                self.rid,
                BASE,
                bearer="fake",
                http=lambda *a: (200, {}, b'{"status":"running"}'),
                wait=lambda _: None,
                polls=1,
            )
        self.assertEqual("unknown", self.ledger.get(self.rid)["state"])

    def test_string_permission_cannot_enable_ocr(self):
        with self.assertRaises(Stopped):
            analyze(
                DATA,
                [1],
                self.ledger,
                self.rid,
                "actor",
                BASE,
                allowed="false",
                reviewed_sha256=digest(DATA),
                http=lambda *a: self.fail("Must not send"),
            )

    def test_malformed_provider_root_remains_held(self):
        def http(method, url, data, bearer):
            if method == "POST":
                return 202, {"Operation-Location": OP}, b""
            return 200, {}, b"[]"

        with self.assertRaises(Stopped):
            self.run_ocr(http=http)
        self.assertEqual("unknown", self.ledger.get(self.rid)["state"])

    def test_completed_cli_resume_needs_no_azure_access_or_credentials(self):
        from unittest.mock import patch
        import contextlib
        import os
        from ocr_project.__main__ import main
        from io import StringIO

        folder = Path(self.tmp.name)
        (folder / "work").mkdir()
        cached = Ledger(folder / "work/usage.db")
        cached.reserve(self.rid, "actor", "hash", 1)
        cached.settle(self.rid, "completed", {"schema": 1, "pages": []})
        cached.close()
        old = Path.cwd()
        try:
            os.chdir(folder)
            with (
                patch.dict(os.environ, {}, clear=True),
                patch("sys.argv", ["ocr", "resume", "--request-id", self.rid]),
                patch(
                    "ocr_project.__main__.token",
                    side_effect=AssertionError("No credential call"),
                ),
                contextlib.redirect_stdout(StringIO()),
            ):
                main()
            self.assertEqual(
                {"schema": 1, "pages": []},
                json.loads(
                    (folder / "work/ocr-pages.json").read_text(encoding="utf-8")
                ),
            )
        finally:
            os.chdir(old)

    def test_ocr_text_rejects_unpaired_unicode_surrogate(self):
        with self.assertRaises(Stopped):
            read_pages(
                {
                    "analyzeResult": {
                        "pages": [{"pageNumber": 1, "lines": [{"content": "\ud800"}]}]
                    }
                },
                [1],
            )

    def test_invalid_page_shapes_stop_before_paid_admission(self):
        for pages in (None, "1", [[1]], [True]):
            with self.subTest(pages=pages), self.assertRaises(Stopped):
                analyze(
                    DATA,
                    pages,
                    self.ledger,
                    self.rid,
                    "actor",
                    BASE,
                    allowed=True,
                    reviewed_sha256=digest(DATA),
                    http=lambda *a: self.fail("No provider call"),
                )
        self.assertEqual(
            0, self.ledger.db.execute("SELECT COUNT(*) FROM usage").fetchone()[0]
        )

    def test_nontext_operation_address_is_controlled(self):
        from ocr_project.pipeline import operation_url

        for address in (None, [], 42):
            with (
                self.subTest(address=address),
                self.assertRaisesRegex(Stopped, "operation address"),
            ):
                operation_url(BASE, address)

    def test_export_never_overwrites_an_existing_source_or_ledger(self):
        from ocr_project.__main__ import save_result

        for name, original in (
            ("source.pdf", b"%PDF-original"),
            ("usage.db", b"SQLite-private"),
        ):
            target = Path(self.tmp.name) / name
            target.write_bytes(original)
            with (
                self.subTest(name=name),
                self.assertRaisesRegex(Stopped, "already contains different data"),
            ):
                save_result(target, {"schema": 1})
            self.assertEqual(original, target.read_bytes())
        self.assertEqual([], list(Path(self.tmp.name).glob(".ocr-result-*")))

    def test_export_identical_result_is_repeat_safe(self):
        from ocr_project.__main__ import save_result

        target = Path(self.tmp.name) / "result.json"
        result = {"schema": 1, "pages": [{"page": 1, "text": "Fictional"}]}
        save_result(target, result)
        first = target.read_bytes()
        save_result(target, result)
        self.assertEqual(first, target.read_bytes())

    def test_export_failure_keeps_directory_free_of_partial_results(self):
        from ocr_project.__main__ import save_result
        from unittest.mock import patch

        target = Path(self.tmp.name) / "new.json"
        with patch(
            "ocr_project.__main__.os.link",
            side_effect=OSError("unsupported filesystem"),
        ):
            with self.assertRaisesRegex(Stopped, "could not be saved safely"):
                save_result(target, {"schema": 1})
        self.assertFalse(target.exists())
        self.assertEqual([], list(Path(self.tmp.name).glob(".ocr-result-*")))

    def test_competing_exports_publish_only_one_complete_result(self):
        from ocr_project.__main__ import save_result
        import concurrent.futures, threading

        target = Path(self.tmp.name) / "race.json"
        barrier = threading.Barrier(2)

        def writer(number):
            barrier.wait(timeout=5)
            try:
                save_result(target, {"value": number})
                return True
            except Stopped:
                return False

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(writer, [1, 2]))
        self.assertEqual(1, sum(outcomes))
        self.assertIn(
            json.loads(target.read_text(encoding="utf-8")), [{"value": 1}, {"value": 2}]
        )
