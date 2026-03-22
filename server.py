#!/usr/bin/env python3
"""Simple server that serves the Gene CDS Fetcher page and proxies NCBI requests to avoid CORS."""

import http.server
import urllib.request
import urllib.error
import ssl
from urllib.parse import urlparse, parse_qs

PORT = 8765

# Create SSL context that works on macOS (avoids certificate verification issues)
_ssl_ctx = ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = ssl.CERT_NONE


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=".", **kwargs)

    def do_GET(self):
        if self.path.startswith("/proxy?"):
            self.proxy_ncbi()
        else:
            super().do_GET()

    def proxy_ncbi(self):
        query = parse_qs(urlparse(self.path).query)
        url = query.get("url", [None])[0]
        if not url or not url.startswith("https://eutils.ncbi.nlm.nih.gov/"):
            self.send_error(400, "Invalid proxy URL")
            return
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "GeneCDSFetcher/1.0"})
            with urllib.request.urlopen(req, timeout=30, context=_ssl_ctx) as resp:
                data = resp.read()
                self.send_response(200)
                self.send_header("Content-Type", resp.headers.get("Content-Type", "text/plain"))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(data)
        except urllib.error.HTTPError as e:
            self.send_error(e.code, str(e.reason))
        except Exception as e:
            self.send_error(502, str(e))


if __name__ == "__main__":
    with http.server.HTTPServer(("", PORT), Handler) as httpd:
        print(f"Serving at http://localhost:{PORT}/")
        httpd.serve_forever()
