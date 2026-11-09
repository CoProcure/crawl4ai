import http.server
import threading
from dataclasses import dataclass

import pytest

HOME_HTML = """<html><head><title>Home Page</title>
<meta name="description" content="The home page">
<meta name="keywords" content="home, welcome">
</head><body>
<h1>Welcome</h1>
<p>{words}</p>
<img src="/team.png" alt="Our team" width="400" height="300">
<a href="/about" title="About us">About us</a>
<a href="/contact">Contact</a>
<a href="https://external.example.org/page">External</a>
</body></html>""".format(words="word " * 50)

PAGE_HTML = "<html><head><title>{title}</title></head><body><p>{words}</p></body></html>"

ROBOTS_TXT = "User-agent: *\nDisallow: /private/\nAllow: /\n"


class LocalSiteHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/robots.txt":
            body, content_type = ROBOTS_TXT, "text/plain"
        elif self.path == "/":
            body, content_type = HOME_HTML, "text/html; charset=utf-8"
        else:
            title = self.path.strip("/").replace("/", " ").title()
            body = PAGE_HTML.format(title=title, words="word " * 50)
            content_type = "text/html; charset=utf-8"

        encoded = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_HEAD(self):
        self.do_GET()

    def log_message(self, format, *args):
        pass


@dataclass
class LocalSite:
    base_url: str

    def url(self, path: str = "/") -> str:
        return f"{self.base_url}{path}"


@pytest.fixture
def local_site():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), LocalSiteHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield LocalSite(base_url=f"http://127.0.0.1:{server.server_port}")
    server.shutdown()
    server.server_close()
