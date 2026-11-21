# Commands

Run these from the project root after creating your files and virtual environment.
Use the README for each command’s prerequisites and expected result; configure optional live calls only when authorized.

```powershell
mkdir azure-document-ocr-python
cd azure-document-ocr-python
python -m venv .venv
```

```powershell
.venv\Scripts\python -m unittest discover -s tests -v
.venv\Scripts\python -m ocr_project demo --output work/ocr-pages.json
```

```powershell
.venv\Scripts\python -m pip install -r requirements-azure.txt
az login
# Inspect infra/README.md and your plan before creating any resource.
```

```powershell
(Get-FileHash .\approved-scan.pdf -Algorithm SHA256).Hash.ToLower()
```

```powershell
$env:ALLOW_AZURE_CALLS = 'yes'
$env:AZURE_DOCUMENT_ENDPOINT = 'https://YOUR-RESOURCE.cognitiveservices.azure.com'
$env:OCR_ESTIMATE_MICRO_PER_PAGE = '1500' # Illustrative, not a current quote.
.venv\Scripts\python -m ocr_project analyze .\approved-scan.pdf --pages 1,2 --actor local-operator --request-id YOUR-UUID --reviewed-sha256 YOUR-SHA256 --allow-ocr
```

```powershell
.venv\Scripts\python -m ocr_project resume --request-id SAME-UUID --output work/ocr-pages.json
```
