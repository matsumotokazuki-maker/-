# -*- coding: utf-8 -*-
r"""emit_status.py — 検証PCが「自分の状態」を1個のJSONに書き出す（読み取り専用）

■ なぜ要るか（2026-08-21・人的介在W1 実装②）
  在庫アプリの正本は検証PC（10.7.45.140）。個人PCは分析用。
  いま2台の同期は **人が pkl を1本コピーする** ことでしか起きない（週次・手作業）。
  ★問題は「コピーを忘れた週に、誰も気づかない」こと。
    2026-08-14 に実際に飛んだ（検証PCで15:42にフルロードが走ったのに個人PCは8/13朝のまま）。
  → **検証PCが自分の状態を書き出し、個人PCが翌朝それを読む**。
    人が運ばなくても「向こうがどうなっているか」だけは届く形にする。

■ 🔴 やらないこと（この装置の線引き）
  - **業務データを一切 書き換えない。** 読むのは health_log.csv / health_rows.csv / session_data.pkl の
    「更新時刻とサイズ」だけ。pkl は **開かない**（重い・壊す risk）。
  - **アプリ本体（inventory_app.py）に触れない。** 現場のタブレットが見ている画面を止めない。
  - **判定しない。** 出すのは事実だけ。読む側（個人PC）が判定する。

■ 使い方
    python emit_status.py                          … 既定の出力先へ書く
    python emit_status.py --out "G:\\...\\status.json"  … 出力先を指定
    python emit_status.py --print                  … 画面に出すだけ（書かない）

■ 出力先の既定（★経路が未決なので、両方に置ける形にしてある）
  1) 共有ドライブ（GDRIVE_OUT）… 見えていれば書く
  2) ローカル（LOCAL_OUT）      … 必ず書く（HTTP配信や手運搬のため）
  ★どちらか一方しか使えなくても動く。**経路の決着を待たずに動かせる**のが狙い。

■ 終了コード
  常に 0。★取込バッチに連鎖させても、絶対に止めない。
"""
import sys, io, os, csv, json, socket, argparse
from datetime import datetime

# ★reconfigure を使う（re-wrap すると、2回目で元の buffer を閉じて
#   "I/O operation on closed file" になる＝2026-08-21 実測で踏んだ）
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(SCRIPT_DIR, "data")
HEALTH_LOG = os.path.join(DATA_DIR, "health_log.csv")
ROW_LOG = os.path.join(DATA_DIR, "health_rows.csv")
PKL = os.path.join(DATA_DIR, "session_data.pkl")

LOCAL_OUT = os.path.join(DATA_DIR, "status.json")
GDRIVE_OUT = r"G:\マイドライブ\DX推進室\鮮魚寿司GO\在庫アプリ\状態\status_kensho.json"

ROW_KEYS = ["prod", "del", "pdel", "plan", "inv", "rcp", "mat"]


def read_csv_rows(path):
    """utf-8-sig / cp932 のどちらでも読む（health_* は utf-8-sig で書かれている）。"""
    if not os.path.exists(path):
        return []
    for enc in ("utf-8-sig", "cp932"):
        try:
            with open(path, "r", encoding=enc, newline="") as f:
                return list(csv.DictReader(f))
        except UnicodeDecodeError:
            continue
        except Exception:
            return []
    return []


def file_stat(path):
    if not os.path.exists(path):
        return {"exists": False}
    st = os.stat(path)
    return {
        "exists": True,
        "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        "size_mb": round(st.st_size / 1024 / 1024, 2),
    }


def build():
    hl = read_csv_rows(HEALTH_LOG)
    hr = read_csv_rows(ROW_LOG)

    last_health = hl[-1] if hl else None
    last_rows = hr[-1] if hr else None

    # ★「実行した日」の一覧＝抜けの判定は読む側に任せる（この装置は判定しない）
    run_dates = []
    for r in hl:
        d = (r.get("点検日") or "").strip()
        if d and d not in run_dates:
            run_dates.append(d)

    rows = {}
    if last_rows:
        for k in ROW_KEYS:
            v = (last_rows.get(k) or "").strip()
            rows[k] = int(v) if v.isdigit() else None

    return {
        "schema": "kensho_status/1",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "host": socket.gethostname(),
        "app_dir": SCRIPT_DIR,
        "health": {
            "last_check_date": (last_health or {}).get("点検日"),
            "state": (last_health or {}).get("状態"),
            "stores": (last_health or {}).get("店舗数"),
            "kari_jan": (last_health or {}).get("仮JAN"),
            "latest_prod": (last_health or {}).get("製造最新"),
            "latest_del": (last_health or {}).get("実納品最新"),
            "latest_inv": (last_health or {}).get("棚卸最新"),
            "dup_cols": (last_health or {}).get("全列重複"),
            "total_checks": len(hl),
        },
        "rows": rows,
        "fingerprint": (last_rows or {}).get("指紋"),
        "pkl": file_stat(PKL),
        "run_dates_recent": run_dates[-40:],
        "files": {
            "health_log": file_stat(HEALTH_LOG),
            "health_rows": file_stat(ROW_LOG),
        },
    }


def write(payload, path):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)          # ★書き換え中を読ませない（原子的に差し替え）
        return True, path
    except Exception as e:
        return False, "%s（%s）" % (path, e)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", help="出力先を1つ指定（指定したらそこだけ書く）")
    ap.add_argument("--print", dest="only_print", action="store_true", help="画面に出すだけ")
    a = ap.parse_args()

    payload = build()

    if a.only_print:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    targets = [a.out] if a.out else [LOCAL_OUT, GDRIVE_OUT]
    print("📤 検証PC 状態の書き出し（%s）" % payload["generated_at"])
    print("   host=%s / 指紋=%s / pkl=%s"
          % (payload["host"], payload["fingerprint"], payload["pkl"].get("mtime")))
    ok_any = False
    for t in targets:
        ok, where = write(payload, t)
        print("   %s %s" % ("✅" if ok else "⚠️", where))
        ok_any = ok_any or ok
    if not ok_any:
        print("   🔴 どこにも書けなかった。★出力先を --out で指定するか、経路を見直す。")
    # ★終了コードは常に0（取込バッチを止めない）


if __name__ == "__main__":
    main()
    sys.exit(0)
