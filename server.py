#!/usr/bin/env python3
"""Serve Gene CDS Fetcher and proxy NCBI E-utilities + BLAST (no browser CORS)."""

import argparse
import http.server
import io
import json
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import zipfile
from urllib.parse import parse_qs, urlparse

PORT = 8765
BLAST_URL = "https://blast.ncbi.nlm.nih.gov/Blast.cgi"

_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE

ALLOWED_EUTILS_PREFIX = "https://eutils.ncbi.nlm.nih.gov/"
ALLOWED_BLAST_PROGRAMS = frozenset({"blastn", "blastx", "blastp"})
ALLOWED_BLAST_DATABASES = frozenset({"refseq_rna", "refseq_protein"})


def _headers_json_cors():
    return [
        ("Content-Type", "application/json; charset=utf-8"),
        ("Access-Control-Allow-Origin", "*"),
    ]


def _headers_plain_cors():
    return [
        ("Content-Type", "text/plain; charset=utf-8"),
        ("Access-Control-Allow-Origin", "*"),
    ]


def blast_put(program, database, query, entrez_query=None, hitlist_size=10):
    params = {
        "CMD": "Put",
        "PROGRAM": program,
        "DATABASE": database,
        "QUERY": query[:500_000],
        "HITLIST_SIZE": str(hitlist_size),
    }
    if entrez_query:
        params["ENTREZ_QUERY"] = entrez_query
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(
        BLAST_URL,
        data=data,
        method="POST",
        headers={
            "User-Agent": "GeneCDSFetcher/1.0",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    with urllib.request.urlopen(req, timeout=120, context=_ssl_ctx) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    m = re.search(r"RID\s*=\s*(\S+)", body)
    if not m:
        raise RuntimeError("BLAST submit did not return RID")
    return m.group(1).strip()


def blast_status(rid):
    url = BLAST_URL + "?" + urllib.parse.urlencode({"CMD": "Get", "RID": rid, "FORMAT_OBJECT": "Status"})
    req = urllib.request.Request(url, headers={"User-Agent": "GeneCDSFetcher/1.0"})
    with urllib.request.urlopen(req, timeout=120, context=_ssl_ctx) as resp:
        return resp.read().decode("utf-8", errors="replace")


def blast_fetch_json2(rid, hitlist_size=10):
    url = BLAST_URL + "?" + urllib.parse.urlencode(
        {
            "CMD": "Get",
            "RID": rid,
            "FORMAT_TYPE": "JSON2",
            "FORMAT_OBJECT": "Alignment",
            "HITLIST_SIZE": str(hitlist_size),
        }
    )
    req = urllib.request.Request(url, headers={"User-Agent": "GeneCDSFetcher/1.0"})
    with urllib.request.urlopen(req, timeout=180, context=_ssl_ctx) as resp:
        return resp.read()


def parse_blast_json2_zip(raw: bytes) -> dict:
    z = zipfile.ZipFile(io.BytesIO(raw))
    inner_names = [n for n in z.namelist() if n.endswith("_1.json")]
    if not inner_names:
        raise ValueError("BLAST zip missing *_1.json")
    j = json.loads(z.read(inner_names[0]).decode("utf-8"))
    bo = j.get("BlastOutput2")
    if not isinstance(bo, dict):
        raise ValueError("Unexpected BlastOutput2 shape")
    report = bo.get("report") or {}
    search = (report.get("results") or {}).get("search") or {}
    qlen = int(search.get("query_len") or 0)
    hits_out = []
    for hit in (search.get("hits") or [])[:10]:
        descs = hit.get("description") or []
        if not descs:
            continue
        desc = descs[0]
        hsps = hit.get("hsps") or []
        if not hsps:
            continue
        hsp = hsps[0]
        alen = int(hsp.get("align_len") or 0)
        ident = int(hsp.get("identity") or 0)
        pct = round(100 * ident / alen, 2) if alen else 0.0
        qfrom = int(hsp.get("query_from") or 0)
        qto = int(hsp.get("query_to") or 0)
        span = abs(qto - qfrom) + 1
        qcov = round(100 * span / qlen, 2) if qlen else 0.0
        hits_out.append(
            {
                "accession": desc.get("accession"),
                "title": desc.get("title"),
                "id": desc.get("id"),
                "evalue": hsp.get("evalue"),
                "bit_score": hsp.get("bit_score"),
                "pct_identity": pct,
                "query_cover_pct": qcov,
                "align_len": alen,
                "query_from": qfrom,
                "query_to": qto,
            }
        )
    return {
        "query_len": qlen,
        "program": report.get("program"),
        "database": (report.get("search_target") or {}).get("db"),
        "hits": hits_out,
    }


def run_blast(program, database, query, entrez_query=None, hitlist_size=10, max_wait_s=300):
    rid = blast_put(program, database, query, entrez_query=entrez_query, hitlist_size=hitlist_size)
    deadline = time.monotonic() + max_wait_s
    sleep = 3.0
    while time.monotonic() < deadline:
        st = blast_status(rid)
        if "Status=READY" in st:
            raw = blast_fetch_json2(rid, hitlist_size=hitlist_size)
            if not raw.startswith(b"PK\x03\x04"):
                raise RuntimeError("BLAST result was not a JSON2 zip bundle")
            parsed = parse_blast_json2_zip(raw)
            parsed["rid"] = rid
            return parsed
        if "Status=FAILED" in st or "Invalid RID" in st:
            raise RuntimeError("BLAST search failed or RID invalid")
        time.sleep(sleep)
    raise TimeoutError("BLAST timed out waiting for results")


class AppHTTPServer(http.server.ThreadingHTTPServer):
    """Shuts down when every /session SSE client has disconnected (optional)."""

    allow_reuse_address = True

    def __init__(self, server_address, RequestHandlerClass, bind_and_activate=True, *, auto_exit=True):
        super().__init__(server_address, RequestHandlerClass, bind_and_activate)
        self.auto_exit = auto_exit
        self._session_lock = threading.Lock()
        self._session_count = 0
        self._ever_had_session = False
        self._shutdown_timer = None

    def add_session(self):
        if not self.auto_exit:
            return
        with self._session_lock:
            self._ever_had_session = True
            if self._shutdown_timer is not None:
                self._shutdown_timer.cancel()
                self._shutdown_timer = None
            self._session_count += 1

    def remove_session(self):
        if not self.auto_exit:
            return
        with self._session_lock:
            self._session_count = max(0, self._session_count - 1)
            if self._session_count == 0 and self._ever_had_session:
                self._schedule_idle_shutdown()

    def _schedule_idle_shutdown(self):
        if self._shutdown_timer is not None:
            self._shutdown_timer.cancel()

        def shutdown_if_still_idle():
            with self._session_lock:
                if self._session_count != 0:
                    return
                print("No open browser tabs (session idle); shutting down server.")
                self._shutdown_timer = None
            self.shutdown()

        self._shutdown_timer = threading.Timer(4.0, shutdown_if_still_idle)
        self._shutdown_timer.daemon = True
        self._shutdown_timer.start()


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=".", **kwargs)

    def handle(self):
        try:
            super().handle()
        except (ConnectionResetError, BrokenPipeError):
            pass

    def handle_one_request(self):
        """Peer closed the socket early; base class would log a full traceback for this."""
        try:
            super().handle_one_request()
        except (ConnectionResetError, BrokenPipeError):
            pass

    def do_OPTIONS(self):
        if self.path.startswith("/api/blast"):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()
            return
        self.send_error(404)

    def do_POST(self):
        if self.path != "/api/blast":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, ValueError):
            self._send_json(400, {"error": "Invalid JSON body"})
            return

        program = payload.get("program")
        database = payload.get("database")
        query = (payload.get("query") or "").strip()
        entrez_query = payload.get("entrez_query")
        hitlist_size = int(payload.get("hitlist_size") or 10)
        max_wait_s = int(payload.get("max_wait_s") or 300)

        if program not in ALLOWED_BLAST_PROGRAMS or database not in ALLOWED_BLAST_DATABASES:
            self._send_json(400, {"error": "Unsupported program or database"})
            return
        if not query or not query.startswith(">"):
            self._send_json(400, {"error": "Query must be FASTA (header line starting with >)"})
            return
        if hitlist_size < 1 or hitlist_size > 50:
            self._send_json(400, {"error": "hitlist_size must be 1–50"})
            return
        if max_wait_s < 30 or max_wait_s > 1800:
            self._send_json(400, {"error": "max_wait_s must be 30–1800"})
            return

        try:
            result = run_blast(
                program,
                database,
                query,
                entrez_query=entrez_query if entrez_query else None,
                hitlist_size=hitlist_size,
                max_wait_s=max_wait_s,
            )
            self._send_json(200, result)
        except TimeoutError as e:
            self._send_json(504, {"error": str(e)})
        except Exception as e:
            self._send_json(502, {"error": str(e)})

    def _send_json(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        for k, v in _headers_json_cors():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if parsed.path == "/session":
            self._session_sse()
            return
        if self.path.startswith("/proxy?"):
            self.proxy_ncbi()
        else:
            super().do_GET()

    def _session_sse(self):
        self.server.add_session()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            while True:
                self.wfile.write(b"data: ping\n\n")
                self.wfile.flush()
                time.sleep(3)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.server.remove_session()

    def proxy_ncbi(self):
        query = parse_qs(urlparse(self.path).query)
        url = query.get("url", [None])[0]
        if not url or not url.startswith(ALLOWED_EUTILS_PREFIX):
            self.send_error(400, "Invalid proxy URL")
            return
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "GeneCDSFetcher/1.0"})
            with urllib.request.urlopen(req, timeout=60, context=_ssl_ctx) as resp:
                data = resp.read()
            self.send_response(200)
            for k, v in _headers_plain_cors():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)
        except urllib.error.HTTPError as e:
            self.send_error(e.code, str(e.reason))
        except Exception as e:
            self.send_error(502, str(e))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Gene CDS Fetcher local server")
    ap.add_argument(
        "--open",
        action="store_true",
        help="Open the app in your default browser after the server starts",
    )
    ap.add_argument(
        "--stay-alive",
        action="store_true",
        help="Keep running after browser tabs close (disable auto-exit)",
    )
    args = ap.parse_args()
    auto_exit = not args.stay_alive

    with AppHTTPServer(("", PORT), Handler, auto_exit=auto_exit) as httpd:
        url = f"http://127.0.0.1:{PORT}/"
        print(f"Serving at {url}")
        if auto_exit:
            print("Auto-exit: server stops ~4s after all tabs to this app are closed.")
        else:
            print("Stay-alive: close this window with Ctrl+C when finished.")
        worker = threading.Thread(target=httpd.serve_forever, daemon=True)
        worker.start()
        if args.open:
            threading.Timer(0.7, lambda: webbrowser.open(url)).start()
        try:
            while worker.is_alive():
                worker.join(timeout=0.5)
        except KeyboardInterrupt:
            print("\nShutting down…")
            httpd.shutdown()
            worker.join(timeout=5)
