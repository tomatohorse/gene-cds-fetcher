# Gene CDS Fetcher

A simple web tool to search NCBI for an organism and gene, then retrieve and display the canonical CDS (coding sequence).

## Features

- **Organism field** – Type any NCBI scientific name (or a taxid). Suggestions cover common organisms; unknown names are resolved via NCBI Taxonomy so BLAST can still use `txidN[ORGN]`.
- **Gene search** – Type a gene symbol (e.g. Ercc1, TP53, BRCA1).
- **Transcript picker** – Lists linked RefSeq RNAs (`NM_`) from NCBI Gene; you choose which transcript before CDS fetch.
- **NCBI integration** – Uses E-utilities (esearch, elink, esummary, efetch) to resolve the gene, summarize transcripts, and download `fasta_cds_na`.
- **Checks panel** (localhost server only) – After each fetch: **ORF** sanity (length, ATG, stops), **blastn** vs RefSeq RNA (same species), **blastx** vs RefSeq protein (same species), and **blastx** vs **human** RefSeq protein (always). Results are **metrics + notes** (no automated pass/fail verdict).
- **Copy to clipboard** – One-click copy of the raw CDS sequence.

## Usage

### Option 1: Run the included server (recommended)

```bash
cd gene-cds-fetcher
python3 server.py
```

Then open http://localhost:8765/ in your browser.

The server serves the page and proxies NCBI requests to avoid CORS issues.

### Option 2: Open the HTML file directly

Open `index.html` in a browser. NCBI JSON endpoints (esearch, elink) work directly, but CDS fetching uses a public CORS proxy (allorigins.win), which may be slower or less reliable.

## Organism suggestions

You can type any scientific name without editing the app. To add a **quick-pick** suggestion, edit the `<datalist id="organismList">` in `index.html` and the matching entry in the `ORGANISM_TAXIDS` map (same file), e.g.:

```html
<option value="Mus musculus" label="Mouse (Mus musculus)"></option>
```

```js
'Mus musculus': 10090,
```

## Technical notes

- NCBI limits requests to about 3 per second without an API key; the app adds short delays between E-utilities calls.
- BLAST runs on NCBI’s servers via `POST /api/blast` (implemented in `server.py`); each search can take **tens of seconds to a few minutes**. Opening the app from `file://` disables BLAST (ORF checks still run in the browser).
- NCBI blocks CORS on text/FASTA responses, so the local server proxies **E-utilities** URLs. BLAST is not proxied as arbitrary URLs; only the server’s `/api/blast` endpoint talks to `blast.ncbi.nlm.nih.gov`.
