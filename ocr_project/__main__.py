import argparse
import json
import os
from pathlib import Path
from .pipeline import analyze, resume, read_pages
from .runtime import Ledger, Stopped, token, read_file
import tempfile


def save_result(path, result):
    """Publish complete JSON without replacing a source, ledger or other file."""
    output = Path(path)
    encoded = json.dumps(result, indent=2, ensure_ascii=False).encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=output.parent, prefix=".ocr-result-", delete=False
        ) as stream:
            temporary = Path(stream.name)
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            # An exclusive hard link exposes only the complete file and never
            # replaces an existing directory entry, even with competing writers.
            os.link(temporary, output)
        except FileExistsError:
            if read_file(output, 8 * 1024 * 1024) != encoded:
                raise Stopped(
                    "The output path already contains different data. Choose a new JSON filename; no file was overwritten. Completed OCR can be exported with resume without resubmitting it."
                ) from None
    except OSError:
        raise Stopped(
            "The OCR JSON could not be saved safely. Check the output directory and filesystem support, then resume the completed request to a new filename; do not submit OCR again."
        ) from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(
        description="Offline OCR fixture or explicitly enabled Azure OCR; never retries a paid submission."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    demo = sub.add_parser("demo")
    demo.add_argument("--output", default="work/ocr-pages.json")
    submit = sub.add_parser("analyze")
    submit.add_argument("file")
    submit.add_argument(
        "--pages", required=True, help="Comma-separated selected page numbers"
    )
    submit.add_argument("--request-id", required=True)
    submit.add_argument("--reviewed-sha256", required=True)
    submit.add_argument("--allow-ocr", action="store_true")
    submit.add_argument("--actor", required=True)
    submit.add_argument("--output", default="work/ocr-pages.json")
    again = sub.add_parser("resume")
    again.add_argument("--request-id", required=True)
    again.add_argument("--output", default="work/ocr-pages.json")
    args = parser.parse_args()
    if args.command == "demo":
        fixture = json.loads(
            Path("examples/provider-response.json").read_text(encoding="utf-8")
        )
        result = {
            "schema": 1,
            "method": "fictional-offline-fixture",
            "source_sha256": None,
            "pages": read_pages(fixture, [1, 2]),
        }
    else:
        if args.command != "resume" and os.environ.get("ALLOW_AZURE_CALLS") != "yes":
            raise Stopped(
                "Live OCR is Off. Set ALLOW_AZURE_CALLS=yes only after reviewing the file, Azure access and costs."
            )
        Path("work").mkdir(exist_ok=True)
        ledger = Ledger(
            "work/usage.db", int(os.environ.get("MONTHLY_PROCESSING_MICRO", "3000000"))
        )
        try:
            base = os.environ.get("AZURE_DOCUMENT_ENDPOINT", "")
            if args.command == "resume":
                saved = ledger.get(args.request_id)
                if saved["state"] == "completed":
                    result = json.loads(saved["result"])
                else:
                    if os.environ.get("ALLOW_AZURE_CALLS") != "yes":
                        raise Stopped(
                            "Live status reads are Off. A saved completed result can be opened offline; pending work needs the original Azure path."
                        )
                    result = resume(ledger, args.request_id, base, bearer=token())
            else:
                path = Path(args.file)
                result = analyze(
                    read_file(path),
                    [int(p) for p in args.pages.split(",")],
                    ledger,
                    args.request_id,
                    args.actor,
                    base,
                    allowed=args.allow_ocr,
                    reviewed_sha256=args.reviewed_sha256,
                    price_micro_per_page=int(os.environ["OCR_ESTIMATE_MICRO_PER_PAGE"]),
                )
        finally:
            ledger.close()
    save_result(args.output, result)
    print("Page-text result saved. Review recognition accuracy before relying on it.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(
            "Stopped:",
            (
                str(error)
                if isinstance(error, Stopped)
                else "Check required settings, page numbers and local file permissions. No automatic retry was made."
            ),
        )
        raise SystemExit(2)
