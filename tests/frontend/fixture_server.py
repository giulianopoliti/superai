"""Local UI fixtures only: no backend, database or external providers."""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/ui", "/ui/"):
            return self.send_file("index.html", "text/html; charset=utf-8")
        if path in ("/ui/app.js", "/ui/styles.css"):
            return self.send_file(path.rsplit("/", 1)[1],
                                  "text/javascript" if path.endswith(".js") else "text/css")
        if path.endswith("/analysis-methods"):
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
        else:
            data = {}
        self.respond(json.dumps(data).encode(), "application/json")

    def do_POST(self):
        self.send_error(405, "Fixture does not accept uploads")

    def send_file(self, filename, content_type):
        self.respond((ROOT / "app" / "static" / filename).read_bytes(), content_type)

    def respond(self, body, content_type):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 8766), Handler).serve_forever()
