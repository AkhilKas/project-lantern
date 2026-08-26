# Project LANTERN

Reproducible ingestion and parsing pipeline for SEC 10-K and 10-Q filings.
Layout-aware, XBRL-validated corpus in Markdown and JSONL, ready for retrieval.

This is Part 1 of a two-part case study. Embeddings and question-answering live
in a separate Part 2 repo.

## Status

Sprint 1 in progress. Parsing pipeline (E2–E5) complete; sprint moves next to
representation and orchestration (E6, E9).

| Epic | Description | Status |
|------|-------------|--------|
| E1   | Bootstrap, EDGAR + XBRL fetch | complete |
| E2   | Text extraction (pdfplumber + OCR fallback) | complete |
| E3   | Table extraction (Camelot lattice + stream) | complete |
| E4   | Layout detection (LayoutParser) | complete |
| E5   | Docling pipeline | complete |
| E6   | Metadata and provenance schema | pending |
| E7   | Storage format comparison (MD/JSON/TXT) | pending |
| E8   | Build vs buy: Gemini comparison | pending |
| E9   | DVC orchestration | pending |
| E10  | Evaluation (WER, table precision/recall) | pending |
| E11  | Cost and throughput benchmarks | pending |
| E12  | XBRL cross-validation (Arelle) | pending |

## Quickstart

Requires Python 3.11 or 3.12 and Git. macOS Apple Silicon is the primary
development target.

```bash
# Clone and install
git clone <this-repo>
cd project-lantern
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Edit params.yaml to set your user_agent_name and user_agent_email,
# then download filings.
lantern download

# Inspect the resolved config
lantern info

# Parsing pipeline (each stage writes to data/interim/<TICKER>/<FORM>/)
lantern parse            # E2: text  -> <accession>.txt
lantern extract-tables   # E3: tables -> <accession>_tables.jsonl
lantern detect-layout    # E4: layout regions -> <accession>_layout.jsonl
lantern docling          # E5: unified pipeline -> <accession>_docling.{jsonl,md}
```

Optional extras (installed only when you reach the relevant epic):

```bash
pip install -e ".[ocr]"      # E2: Tesseract OCR fallback
pip install -e ".[tables]"   # E3: Camelot
pip install -e ".[layout]"   # E4: LayoutParser (EfficientDet backend)
pip install -e ".[docling]"  # E5: Docling
pip install -e ".[xbrl]"     # E12: Arelle
pip install -e ".[managed]"  # E8: Gemini
```

## Project structure

```
src/lantern/
  config.py        - params.yaml loader (Pydantic)
  cli.py           - click CLI entrypoint
  ingest/          - EDGAR + XBRL download
  parse/           - pdfplumber, Camelot, LayoutParser, Docling (per epic)
  represent/       - metadata schema, MD/JSON/TXT exporters
  managed/         - Gemini "buy" path
  validate/        - XBRL alignment
  eval/            - WER, table metrics, benchmarks
data/              - DVC-tracked outputs (not in git)
reports/           - human-readable analysis reports
tests/             - pytest suite
.github/workflows/ - CI smoke test
```

## Companies and filings

Five Fortune 500 companies across different sectors, to stress-test the
pipeline on varied layouts:

| Ticker | Company         | Sector       |
|--------|-----------------|--------------|
| AAPL   | Apple           | Technology   |
| MSFT   | Microsoft       | Technology   |
| JPM    | JPMorgan Chase  | Financials   |
| XOM    | ExxonMobil      | Energy       |
| WMT    | Walmart         | Retail       |

One 10-K and one 10-Q per company, 10 filings total. Configurable in
`params.yaml`.

## Development

```bash
pre-commit install   # one-time
pytest               # run the test suite
ruff check src tests # lint
black src tests      # format
```

CI runs ruff, black, and pytest on every push and PR to main.

## License

MIT.
