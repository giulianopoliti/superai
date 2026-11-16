"""Local UI fixtures only: no backend, database or external providers."""
import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/ui/config.js":
            return self.respond(
                b'window.STOCK_AI_CONFIG = '
                b'{authEnabled:false,jobsEnabled:false,apiBaseUrl:""};', "text/javascript"
            )
        if path == "/ui/auth.js":
            return self.respond(b'/* Offline fixture: no identity provider. */', "text/javascript")
        if path in ("/", "/ui", "/ui/", "/ui/index.html"):
            return self.send_file("index.html", "text/html; charset=utf-8")
        if path == "/ui/super-salvador.png":
            return self.send_file("super-salvador.png", "image/png")
        if path in ("/ui/app.js", "/ui/styles.css", "/ui/transport.js", "/ui/jobs.js"):
            return self.send_file(path.rsplit("/", 1)[1],
                                  "text/javascript" if path.endswith(".js") else "text/css")
        if path in {"/operations/dashboard", "/operations/home"}:
            data = {"business_id": "demo-business", "date_from": "2026-10-09",
                    "date_to": "2026-10-15", "sales_status": {"last_sale_date": None},
                    "sales_totals": {"revenue": "0", "ticket_count": 0, "quantity_sold": "0"},
                    "alerts": [], "pending_reminders": [], "overdue_reminders": [],
                    "supplier_offers": [], "top_products_by_quantity": []}
        elif path == "/sales/status":
            data = {"last_sale_date": None, "total_sale_lines": 0}
        elif path == "/procurement/catalog-imports/status":
            data = {"last_import": None, "total_products": 0}
        elif path.endswith("/analysis-methods"):
            data = {"contract_version": "supplier_offer_analysis_v1",
                    "default_method": "openai_structured", "methods": [
                        {"id": "openai_structured", "available": True},
                        {"id": "openai_blocks_jev", "available": False,
                         "unavailable_reason": "Método B no integrado en esta prueba."}]}
        elif path.endswith("/summaries"):
            data = []
        elif path.endswith("/analysis"):
            data = {"document_id": "fixture", "analysis": {
                "method": "openai_blocks_jev", "status": "partial",
                "pending_block_count": 1, "unlinked_offer_count": 2,
                "identity_review_offer_count": 2, "price_review_offer_count": 1},
                "pending_blocks": [{"page_number": 1,
                    "text": '<img src=x onerror=alert(1)>\n' + "Texto largo " * 35,
                    "reason": 'Falta confirmar "precio".'}]}
        elif path.endswith("/matches"):
            data = {"candidates": []}
        elif path.endswith("/purchase-plan"):
            data = {"business_id": "demo-business", "supplier_offer_document_id": "fixture",
                    "date_from": "2026-10-09", "date_to": "2026-10-15",
                    "target_coverage_days": None, "sort_by": "sales_then_savings", "items": []}
        else:
            return self.send_error(404, "No fixture for this route")
        self.respond(json.dumps(data).encode(), "application/json")

    def do_POST(self):
        self.send_error(405, "Fixture does not accept uploads")

    def send_file(self, filename, content_type):
        self.respond((ROOT / "app" / "static" / filename).read_bytes(), content_type)

    def respond(self, body, content_type):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
