"""Servidor local de desenvolvimento: faz o papel do Caddy de produção.

Serve o bundle do frontend (`web/`) com fallback de SPA para `index.html` e
repassa `/api/*` e `/saude` para a API (uvicorn). Só biblioteca padrão.

    python scripts/servir_web.py                       # http://127.0.0.1:8080 -> API em 127.0.0.1:8001
    GPS_WEB_PORTA=8090 GPS_API=http://127.0.0.1:8002 python scripts/servir_web.py

Não use em produção: veja deploy/vps/gpsproject.caddy.
"""
from __future__ import annotations

import os
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
WEB = Path(os.environ.get("GPS_WEB_DIR", RAIZ / "web"))
API = os.environ.get("GPS_API", "http://127.0.0.1:8001").rstrip("/")
PORTA = int(os.environ.get("GPS_WEB_PORTA", "8080"))
SEM_REPASSE = {"connection", "keep-alive", "transfer-encoding", "content-encoding", "content-length"}


class Manipulador(SimpleHTTPRequestHandler):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, directory=str(WEB), **kwargs)  # type: ignore[arg-type]

    def _para_api(self) -> bool:
        return self.path.startswith("/api/") or self.path.split("?")[0] == "/saude"

    def _repassar(self) -> None:
        tamanho = int(self.headers.get("Content-Length") or 0)
        corpo = self.rfile.read(tamanho) if tamanho else None
        cabecalhos = {k: v for k, v in self.headers.items() if k.lower() not in SEM_REPASSE | {"host"}}
        req = urllib.request.Request(API + self.path, data=corpo, method=self.command, headers=cabecalhos)
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                status, hdrs, dados = r.status, r.getheaders(), r.read()
        except urllib.error.HTTPError as e:
            status, hdrs, dados = e.code, e.headers.items(), e.read()
        except urllib.error.URLError as e:
            self.send_error(502, f"API indisponível em {API}: {e.reason}")
            return
        self.send_response(status)
        for k, v in hdrs:
            if k.lower() not in SEM_REPASSE:
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)

    def do_GET(self) -> None:
        if self._para_api():
            self._repassar()
            return
        caminho = WEB / self.path.split("?")[0].lstrip("/")
        if not caminho.exists() or caminho.is_dir() and not (caminho / "index.html").exists():
            self.path = "/index.html"  # rotas da SPA são client-side
        super().do_GET()

    def do_POST(self) -> None:
        self._repassar()

    do_PUT = do_PATCH = do_DELETE = do_POST

    def end_headers(self) -> None:
        if not self._para_api() and self.path.endswith(("index.html", "sw.js", "manifest.webmanifest")):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()


if __name__ == "__main__":
    print(f"GPlanSiS|Project local em http://127.0.0.1:{PORTA}  (web: {WEB}, API: {API})")
    ThreadingHTTPServer(("127.0.0.1", PORTA), Manipulador).serve_forever()
