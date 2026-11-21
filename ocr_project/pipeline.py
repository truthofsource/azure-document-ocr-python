"""Document Intelligence prebuilt-read adapter with a saved operation receipt."""

from __future__ import annotations
import base64
import json
import time
import urllib.parse
import uuid
from .runtime import Ledger, Stopped, digest, endpoint, request, token

VERSION = "2024-11-30"
PREFIX = "/documentintelligence/documentModels/prebuilt-read/analyzeResults/"


def validate_file(data, pages):
    pdf = data.startswith(b"%PDF-")
    image = data.startswith(b"\x89PNG\r\n\x1a\n") or data.startswith(b"\xff\xd8\xff")
    if not data or len(data) > 10 * 1024 * 1024 or not (pdf or image):
        raise Stopped(
            "Use a reviewed PDF, PNG or JPG no larger than 10 MiB. A file extension alone is not sufficient."
        )
    if (
        not isinstance(pages, list)
        or not pages
        or len(pages) > 50
        or any(type(p) is not int or not 1 <= p <= 500 for p in pages)
        or len(set(pages)) != len(pages)
    ):
        raise Stopped("Choose 1–50 distinct page numbers between 1 and 500.")
    if image and pages != [1]:
        raise Stopped("A PNG/JPG must use page 1 only.")


def operation_url(base, value):
    if not isinstance(value, str):
        raise Stopped(
            "OCR operation address is missing or malformed; preserve the held request for investigation."
        )
    parsed = urllib.parse.urlsplit(value)
    tail = parsed.path[len(PREFIX) :] if parsed.path.startswith(PREFIX) else ""
    try:
        valid_id = str(uuid.UUID(tail)) == tail.lower()
    except ValueError:
        valid_id = False
    if (
        parsed.scheme + "://" + parsed.netloc != base
        or parsed.fragment
        or not valid_id
        or parsed.query != "api-version=" + VERSION
    ):
        raise Stopped(
            "Azure operation address did not match the configured resource and API version. The request remains held."
        )
    return value


def read_pages(value, selected):
    try:
        pages = value["analyzeResult"]["pages"]
        if len(pages) != len(selected):
            raise ValueError()
        out, seen, total = [], set(), 0
        for page in pages:
            number, lines = page["pageNumber"], page.get("lines", [])
            if (
                type(number) is not int
                or number not in selected
                or number in seen
                or not isinstance(lines, list)
            ):
                raise ValueError()
            seen.add(number)
            if len(lines) > 50_000 or any(
                not isinstance(line.get("content"), str) for line in lines
            ):
                raise ValueError()
            text = "\n".join(line["content"] for line in lines)
            text.encode("utf-8", errors="strict")
            total += len(text)
            if total > 1_000_000:
                raise ValueError()
            out.append({"page": number, "text": text})
        return sorted(out, key=lambda p: p["page"])
    except (KeyError, TypeError, ValueError, AttributeError, UnicodeError):
        raise Stopped(
            "OCR output did not match all requested pages or exceeded text limits. No partial result was published."
        ) from None


def resume(
    ledger: Ledger, request_id, base, *, bearer, http=request, wait=time.sleep, polls=45
):
    base = endpoint(base, "cognitiveservices.azure.com")
    row = ledger.get(request_id)
    if row["state"] == "completed":
        return json.loads(row["result"])
    if row["state"] not in ("reserved", "unknown"):
        raise Stopped("Only a saved pending/uncertain operation can be resumed.")
    saved = json.loads(row["operation"] or "{}")
    if not isinstance(saved, dict) or set(saved) != {"url", "pages", "sha256"}:
        raise Stopped(
            "No valid operation receipt was saved. Investigate the uncertain request; do not submit it again."
        )
    url = operation_url(base, saved["url"])
    for _ in range(polls):
        status, _, body = http("GET", url, None, bearer)
        if status != 200:
            raise Stopped(
                "OCR status read failed. The paid operation remains held; resume only this saved operation."
            )
        try:
            value = json.loads(body)
        except (ValueError, UnicodeError):
            raise Stopped(
                "OCR returned unreadable JSON; the operation remains held."
            ) from None
        if not isinstance(value, dict):
            raise Stopped(
                "OCR returned an unexpected JSON structure; the operation remains held."
            )
        if value.get("status") in ("running", "notStarted"):
            wait(1)
            continue
        if value.get("status") != "succeeded":
            ledger.settle(request_id, "unknown")
            raise Stopped(
                "Azure did not report a successful analysis. Review its operation status and billing before another submission."
            )
        output = {
            "schema": 1,
            "source_sha256": saved["sha256"],
            "pages": read_pages(value, saved["pages"]),
            "method": "azure-document-intelligence-prebuilt-read",
        }
        ledger.settle(request_id, "completed", output)
        return output
    ledger.settle(request_id, "unknown")
    raise Stopped(
        "OCR is still pending. Resume the saved operation later; do not pay for a duplicate submission."
    )


def analyze(
    data,
    pages,
    ledger,
    request_id,
    actor,
    base,
    *,
    allowed=False,
    reviewed_sha256="",
    price_micro_per_page=1500,
    http=request,
    get_token=token,
    wait=time.sleep,
):
    validate_file(data, pages)
    base = endpoint(base, "cognitiveservices.azure.com")
    if allowed is not True or reviewed_sha256 != digest(data):
        raise Stopped(
            "OCR must be explicitly allowed and the reviewed-file SHA-256 must match the exact bytes. No document was sent."
        )
    if type(price_micro_per_page) is not int or price_micro_per_page <= 0:
        raise Stopped(
            "Configure a reviewed positive per-page estimate before live OCR."
        )
    fingerprint = digest(data + json.dumps(pages).encode())
    ledger.reserve(request_id, actor, fingerprint, len(pages) * price_micro_per_page)
    sent = False
    try:
        bearer = get_token()
        url = (
            base
            + "/documentintelligence/documentModels/prebuilt-read:analyze?api-version="
            + VERSION
            + "&pages="
            + ",".join(map(str, pages))
        )
        # JSON/base64 is documented for this API. Only the selected pages are processed,
        # but the entire file is transmitted; page selection is not data redaction.
        payload = json.dumps(
            {"base64Source": base64.b64encode(data).decode("ascii")}
        ).encode()
        sent = True
        status, headers, _ = http("POST", url, payload, bearer)
        location = next(
            (v for k, v in headers.items() if k.lower() == "operation-location"), ""
        )
        if status != 202:
            raise Stopped(
                "Azure did not accept OCR as expected; the request is held for review."
            )
        location = operation_url(base, location)
        ledger.receipt(
            request_id,
            json.dumps({"url": location, "pages": pages, "sha256": digest(data)}),
        )
        return resume(ledger, request_id, base, bearer=bearer, http=http, wait=wait)
    except BaseException:
        if ledger.get(request_id)["state"] != "completed":
            ledger.settle(request_id, "unknown" if sent else "not_sent")
        raise
