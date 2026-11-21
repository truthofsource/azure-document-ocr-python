# Azure Document OCR with Python — Build Your Own


### Prerequisites

Use Python 3.11 or newer and your own fictional test files. Azure and external AI are optional; live examples require your own approved account, network path and reviewed processing costs.

Commands run from your new project root; use PowerShell for the shown environment-variable syntax. Follow the named source files as implementation references without needing to clone a portfolio repository.


### Step 1 — Create your project folder

Create an empty folder named `azure-document-ocr-python` and add `ocr_project`,
`examples`, `tests` and `docs` subfolders. Use the included Python files as small
implementation references while building the components below; no repository
clone is required.

```powershell
mkdir azure-document-ocr-python
cd azure-document-ocr-python
python -m venv .venv
```

Commands below run from that folder after placing the accompanying files in
their indicated paths. On Windows use `.venv\Scripts\python`; on Linux/macOS
substitute `.venv/bin/python`.

### Step 2 — Separate local checks from Azure execution

Implement file/page checks and the atomic usage ledger before adding a service
call. Keep the optional Azure dependency separate so a learner can run the
fictional demo and tests without credentials or cloud charges.

```powershell
.venv\Scripts\python -m unittest discover -s tests -v
.venv\Scripts\python -m ocr_project demo --output work/ocr-pages.json
```

### Step 3 — Preserve page identity and empty pages

Parse the fixture in `examples/provider-response.json` into `{page,text}`
records and reject missing, repeated or unexpected page numbers. Keep a truly
blank page as empty text, and review recognition accuracy rather than assuming
every successful provider response is correct.

The demo writes `method: fictional-offline-fixture` and no source hash. It is
intentionally unsuitable as genuine OCR evidence for the RAG importer.

### Step 4 — Prepare the Azure service and identity

In Azure, prepare a Document Intelligence account and give a dedicated caller
the account-scoped `Cognitive Services User` role. Use the optional Terraform
example with an existing private subnet/DNS zone, then verify private resolution
and endpoint reachability from the intended caller before enabling live mode.

```powershell
.venv\Scripts\python -m pip install -r requirements-azure.txt
az login
# Inspect infra/README.md and your plan before creating any resource.
```

### Step 5 — Bind consent to the exact approved file

Review the whole file, verify its genuine clean-scan result outside this local
sample, and record its SHA-256 plus the pages to process. The API sends the
**entire file** even when only selected pages are analyzed, so approve that
transfer explicitly and use a reviewed current per-page cost estimate.

```powershell
(Get-FileHash .\approved-scan.pdf -Algorithm SHA256).Hash.ToLower()
```

### Step 6 — Add one deliberate Azure submission

Set the endpoint and estimate in your own environment, then call `analyze`
with a fresh UUID only for a genuinely new request. Keep live execution Off
until the source, access, network and costs are ready; the following is an
operator-run example and was not executed against Azure for this project.

```powershell
$env:ALLOW_AZURE_CALLS = 'yes'
$env:AZURE_DOCUMENT_ENDPOINT = 'https://YOUR-RESOURCE.cognitiveservices.azure.com'
$env:OCR_ESTIMATE_MICRO_PER_PAGE = '1500' # Illustrative, not a current quote.
.venv\Scripts\python -m ocr_project analyze .\approved-scan.pdf --pages 1,2 --actor local-operator --request-id YOUR-UUID --reviewed-sha256 YOUR-SHA256 --allow-ocr
```

### Step 7 — Save the receipt before waiting for completion

Persist the validated `Operation-Location` before polling and accept only the
configured Azure origin, model-result path and API version. If waiting fails
after that receipt exists, resume its GET status checks without submitting
another paid POST.

```powershell
.venv\Scripts\python -m ocr_project resume --request-id SAME-UUID --output work/ocr-pages.json
```

If the result is already saved as completed, `resume` can restore its JSON
output with live calls Off and without Azure credentials. Pending/unknown
operations still require the original authorized endpoint and live-read setting.

Each status request has a 25-second transport timeout and at most 45 polls per
invocation. This is not a 45-second end-to-end deadline, and credential retrieval
has its own SDK timing; use a proper wall-clock deadline in a hosted worker.

### Step 8 — Review the output and hand it to search

Inspect the page-text JSON against the original scan, especially names, dates,
tables and faint text. Keep its `source_sha256` and expected Azure method label
when passing it to the companion RAG project's explicit OCR importer, while
retaining the original PDF and controlling access to both.
