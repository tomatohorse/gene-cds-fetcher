# Gene CDS Fetcher

A simple web tool to search NCBI for an organism and gene, then retrieve and display the canonical CDS (coding sequence).

## Features

- **Organism dropdown** – Select from common model organisms (Mouse, Human, Rat, Fruit fly, Yeast). You can add more options by editing the `<select>` in `index.html`.
- **Gene search** – Type a gene symbol (e.g. Ercc1, TP53, BRCA1).
- **NCBI integration** – Uses NCBI E-utilities (esearch, elink, efetch) to find the gene and its RefSeq RNA, then fetches the CDS.
- **Copy to clipboard** – One-click copy of the sequence.

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

## Adding organisms

Edit the `<select id="organism">` in `index.html` and add options like:

```html
<option value="Scientific name">Common name (Scientific name)</option>
```

The `value` must be the organism’s scientific name as used by NCBI (e.g. `Mus musculus`, `Homo sapiens`).

## Technical notes

- NCBI limits requests to about 3 per second without an API key; the app adds delays between CDS fetches.
- The first RefSeq RNA with a CDS is used; for genes with multiple transcripts, this may not be the primary isoform.
- NCBI blocks CORS on text/FASTA responses, so a proxy is used for CDS fetching.
