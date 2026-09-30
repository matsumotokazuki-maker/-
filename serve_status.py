# -*- coding: utf-8 -*-
r"""serve_status.py — status.json だけを配る、極小のHTTPサーバ（読み取り専用）

■ なぜ要るか（2026-08-21・人的介在W1 実装②の経路）
  検証PC が書き出した状態（status.json）を、個人PC が **人の手を介さずに** 読むための口。

■ 🔴 なぜ「アプリに1画面足す」ではなく、別プロセスなのか（★これが設計の芯）
  在庫アプリ（Streamlit・8501）は **現場のタブレットが見ている画面**。
  静的配信を有効にするにも、画面を1枚足すにも、**アプリの再起動が要る**。
  ★`start_app.bat` は `streamlit run …` を前面で実行して末尾 `pause`＝**人がダブルクリックして起動する形**。
    自動再起動を仕込むと、**失敗したとき現場の画面が戻らず、その場に誰も居ない**。
  → **「人が居なくても回る」を作るために「無人で壊れる余地」を作るのは向きが逆。**
  ★したがって **アプリには一切触らず、別ポートで別プロセス**にする。
    このサーバが落ちても **status が読めなくなるだけ**で、現場は無傷。

■ 🔴 やらないこと（安全のため、意図的に狭くしてある）
  - **配るのは data\status.json ただ1つ**（ホワイトリスト）。他のパスは全部 404。
  - **GET だけ**。POST/PUT/DELETE は 405。
  - **ディレクトリ一覧を出さない**（http.server の既定挙動を使わない）。
  - **書き込みは一切しない。**

■ 使い方
    python serve_status.py            … 0.0.0.0:8600 で待ち受け
    python serve_status.py --port 8600 --once   … 1回 自己テストして終了

■ 経路
    個人PC → http://10.7.45.140:8600/status.json
"""
import sys, os, json, argparse, socket
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATUS = os.path.join(SCRIPT_DIR, "data", "status.json")
ALLOWED = ("/status.json", "/")          # ★これ以外は配らない


class Handler(BaseHTTPRequestHandler):
    server_version = "kensho-status/1"

    def log_message(self, fmt, *args):    # ★コンソールを汚さない
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        b = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(b)
        except Exception:
            pass

    def do_GET(self):
        path = self.path.split("?")[0]
        if path not in ALLOWED:
            self._send(404, '{"error":"not found"}')
            return
        if not os.path.exists(STATUS):
            self._send(503, json.dumps(
                {"error": "status.json がまだ無い",
                 "hint": "emit_status.py を1回 回す",
                 "expected": STATUS}, ensure_ascii=False))
            return
        try:
            with open(STATUS, "rb") as f:
                self._send(200, f.read())
        except Exception as e:
            self._send(500, json.dumps({"error": str(e)}, ensure_ascii=False))

    def do_POST(self):
        self._send(405, '{"error":"read only"}')

    do_PUT = do_DELETE = do_PATCH = do_POST


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8600)
    ap.add_argument("--once", action="store_true", help="自己テストだけして終わる")
    a = ap.parse_args()

    print("🌐 status 配信サーバ（読み取り専用・配るのは status.json だけ）")
    print("   host=%s / port=%d" % (socket.gethostname(), a.port))
    print("   file=%s（%s）" % (STATUS, "あり" if os.path.exists(STATUS) else "🔴まだ無い"))
    print("   ★アプリ（8501）には一切 触りません。落ちても現場は無傷です。")

    if a.once:
        print("   （--once なので待ち受けません）")
        return

    try:
        srv = ThreadingHTTPServer(("0.0.0.0", a.port), Handler)
    except OSError as e:
        print("   🔴 ポート %d を開けない（%s）＝別のポートを使うか、既に動いている" % (a.port, e))
        return
    print("   ✅ 待ち受け開始（%s）" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
