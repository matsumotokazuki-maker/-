"""
update_data.py
================
input_raw フォルダの中身を見て、フルロード or 差分追加 を自動判定して処理。

【動作モード】

(A) フルロード（月初・棚卸後）
    input_raw に基本7ファイルが揃っている場合
      原料データ.csv / レシピデータ.csv / 計画数データ.csv
      製造数データ.csv / 納品予定数.csv / 実納品数データ.csv / 棚卸データ.csv
    → 既存pklは無視、新データで完全置き換え

(B) 差分追加（日次）
    input_raw に日付付きファイル（YYYYMMDD<種別>.csv）がある場合
      例：20260507製造数.csv、20260507実納品数.csv、20260507棚卸.csv
    → 既存pklに対して、同じキー（店舗×原料×日付）なら上書き、なければ追加

(C) 半端な状態
    どちらでもないとき → エラー＋ヒント表示

加工ロジック（フルロード/差分共通）:
- 原料名／商品名を正規化キー化（NFKC・空白除去・記号統一）
- 原料マスタの正しい13桁JANに突合
- マッチしないものには仮JAN（9991xxxx / 9992xxxx）を付与

使い方:
  update_data.bat をダブルクリック、または
  python update_data.py
"""
import sys
import re
import unicodedata
import pickle
import datetime
from pathlib import Path

try:
    import pandas as pd
except ImportError:
    print("\n[ERROR] pandas がインストールされていません。")
    print("コマンドプロンプトで以下を実行してください:")
    print("  pip install pandas")
    input("\nEnter キーで終了...")
    sys.exit(1)


# ========== パス設定 ==========
BASE_DIR = Path(__file__).parent
RAW_DIR = BASE_DIR / 'input_raw'
OUT_DIR = BASE_DIR / 'input_processed'
DATA_DIR = BASE_DIR / 'data'
SAVE_PATH = DATA_DIR / 'session_data.pkl'

# フルロード判定に使う基本7ファイル
FULL_FILES = [
    '原料データ.csv',
    'レシピデータ.csv',
    '計画数データ.csv',
    '製造数データ.csv',
    '納品予定数.csv',
    '実納品数データ.csv',
    '棚卸データ.csv',
]

# ===== [Phase2 包材単位] ボウル→枚 正規化 =====
# 背景: 業務側が 6/18 に包材の納品系(納品予定数・実納品数)を 枚→ボウル(=入数単位)へ変更(松本さんQ2)。
#   アプリの包材在庫は 入庫/出庫/棚卸 すべて「枚」基準(inventory_app.py の is_houzai)。
#   → 取込時に包材の納品系を 枚 へ正規化(数量×入数)して整合させる。
# 適用: actual_del/plan_del の「包材 ∧ 納品予定日>=6/18」のみ。棚卸・製造数・非包材は不変。
#   plan_del も日付ゲート(≥6/18)。理由: バックアップpklで plan_del が5月は枚だったと確認(過剰換算回避)。
# 冪等性: 本関数は「新規に読み込んだCSV行(フル=全行/差分=new_df)」にのみ適用すること。
#   save_loaded_to_pkl(マージ後の全loadedを毎回処理)には絶対に置かない(再乗算で二重膨張)。
HOUZAI_KUBUN = {'包材(本体)', '包材（蓋）', '包材(内装)', 'シール'}  # inventory_app.py:85 と一致必須
HOUZAI_UNIT_CUTOVER = datetime.date(2026, 6, 18)  # この日以降の納品系包材をボウル→枚換算

def _build_houzai_nyusu_map(material_df):
    """包材JAN -> 入数(>0) の辞書。入数<=0/欠損は除外(=変換対象から外す=no-op)。"""
    if material_df is None or '原料区分' not in material_df.columns:
        return {}
    m = material_df.copy()
    jan_col = '原料JAN' if '原料JAN' in m.columns else 'JAN'
    is_hz = m['原料区分'].astype(str).isin(HOUZAI_KUBUN)
    nyusu = {}
    for _, r in m[is_hz].iterrows():
        n = pd.to_numeric(r.get('入数', 1), errors='coerce')
        if pd.isna(n) or n <= 0:
            continue  # 入数不明は換算しない(安全側=no-op)
        nyusu[str(r[jan_col]).strip()] = float(n)
    return nyusu

def convert_houzai_units(df, kind, material_df):
    """包材の納品系数量を ボウル→枚 に正規化(×入数)。新規CSV行に対してのみ呼ぶこと。
    kind='plan_del'(納品予定数) / 'actual_del'(仕入計上数)。いずれも 納品予定日>=6/18 のみ。
    戻り値: (変換後df, 変換件数)。非対象kind・空df・対象0件なら df をそのまま返す。"""
    if kind not in ('plan_del', 'actual_del') or df is None or len(df) == 0:
        return df, 0
    qty_col = '納品予定数' if kind == 'plan_del' else '仕入計上数'
    if qty_col not in df.columns or 'JAN' not in df.columns or '納品予定日' not in df.columns:
        return df, 0
    nyusu = _build_houzai_nyusu_map(material_df)
    if not nyusu:
        return df, 0
    df = df.copy()
    jan = df['JAN'].astype(str).str.strip()
    is_hz_row = jan.isin(nyusu)
    d = df['納品予定日'].apply(parse_jp_date)
    gate = d.apply(lambda x: (x is not None) and (x >= HOUZAI_UNIT_CUTOVER))
    target = is_hz_row & gate
    n = int(target.sum())
    if n:
        factor = jan.map(nyusu)
        vals = pd.to_numeric(df[qty_col], errors='coerce').fillna(0).astype(float)
        # 数量列は read_csv(dtype=str) 由来で文字列型（pandas 3.0 では pyarrow string）。
        # 文字列列に int64 を代入すると pyarrow backend が TypeError を出すため、
        # 計算結果を文字列に戻して代入する（旧 object 列・新 string 列の両方で安全。
        # pipeline 全体が数量を文字列で扱う規約とも一致＝対象行のみ変化／列dtypeは不変）。
        new_vals = (vals[target] * factor[target]).round().astype('int64').astype(str)
        df.loc[target, qty_col] = new_vals
        print(f"    [Phase2 包材単位] {kind}: {n}行を ボウル→枚 換算(×入数, 納品予定日>={HOUZAI_UNIT_CUTOVER})")
    return df, n

# 差分追加が許可されている種別（ファイル名末尾のキーワード）
DIFF_ALLOWED_KINDS = {
    '製造数': 'actual_prod',
    '実納品数': 'actual_del',
    '棚卸': 'inventory',
    '納品予定数': 'plan_del',  # 2026/5/18 追加：最新の納品計画を差分追加できるように
}


# ========== 正規化関数 ==========
def normalize_name(s):
    if pd.isna(s):
        return ''
    s = str(s)
    s = unicodedata.normalize('NFKC', s)
    s = re.sub(r'\s+', '', s)
    s = re.sub(r'[_\-\u2010\u2014\uff3f\uff0d\u30fc/\uff0f\u30fb]', '', s)
    s = s.replace('\uff08', '(').replace('\uff09', ')')
    s = s.replace('\u300c', '').replace('\u300d', '')
    s = s.replace('\u3010', '').replace('\u3011', '')
    s = s.upper()
    return s


def parse_jp_date(s, year=2026):
    if pd.isna(s):
        return None
    s = str(s).strip()
    if not s:
        return None
    for sep in ['/', '-']:
        if sep in s:
            try:
                parts = [int(x) for x in s.split(sep)]
                if len(parts) == 3:
                    if parts[0] > 1900:
                        return datetime.date(parts[0], parts[1], parts[2])
                    else:
                        return datetime.date(parts[2], parts[0], parts[1])
                elif len(parts) == 2:
                    return datetime.date(year, parts[0], parts[1])
            except (ValueError, IndexError):
                pass
    return None


# ========== ヘルパー ==========
def print_section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print('='*60)


def read_csv_safely(path):
    df = pd.read_csv(path, dtype=str)
    df.columns = df.columns.str.strip().str.replace('\ufeff', '')
    unnamed = [c for c in df.columns if c.startswith('Unnamed:')]
    if unnamed:
        df = df.drop(columns=unnamed)
    # JAN/CD系の列に紛れ込む末尾 .0（数値化由来）を除去
    # (2026-05-30 追加：'2831000052052.0' のような浮動小数文字列を13桁に戻す＝バグ3保険)
    for c in df.columns:
        if ('JAN' in c) or (c in ('店舗CD', '店CD', 'CD')):
            df[c] = df[c].astype(str).str.replace(r'^(\d+)\.0+$', r'\1', regex=True)
            df[c] = df[c].replace({'nan': '', 'None': ''})
    return df


def _strip_all(df):
    for c in df.columns:
        if df[c].dtype == 'object':
            df[c] = df[c].astype(str).str.strip()
    return df


# ========== モード判定 ==========
def detect_mode():
    """input_raw フォルダの中身を見て、モードを判定"""
    if not RAW_DIR.exists():
        return 'no_folder', None
    
    files = list(RAW_DIR.glob('*.csv'))
    if not files:
        return 'empty', None
    
    file_names = [f.name for f in files]
    
    # フルロード判定：基本7ファイルが全部ある
    has_full = all(name in file_names for name in FULL_FILES)
    
    # 差分判定：YYYYMMDD<種別>.csv のパターンがある
    diff_pattern = re.compile(r'^(\d{8})(.+)\.csv$')
    diff_files = []
    for f in files:
        m = diff_pattern.match(f.name)
        if m:
            date_str = m.group(1)
            kind_str = m.group(2)
            # 種別名が DIFF_ALLOWED_KINDS にあるかチェック
            for k in DIFF_ALLOWED_KINDS:
                if k in kind_str:
                    diff_files.append((f, date_str, DIFF_ALLOWED_KINDS[k]))
                    break
    
    if has_full and not diff_files:
        return 'full', None
    if diff_files and not has_full:
        return 'diff', diff_files
    if has_full and diff_files:
        # 両方ある場合：原則フルロードを優先するか、エラーにする
        return 'mixed', diff_files
    
    return 'invalid', file_names


# ========== JAN加工コア ==========
def build_jan_resolvers(mat_df, rec_df):
    """原料マスタとレシピから、JAN解決用のクロージャを返す"""
    mat_df = mat_df.copy()
    mat_df['原料JAN'] = mat_df['原料JAN'].str.strip()
    mat_df['原料名_orig'] = mat_df['原料名'].astype(str).str.strip()
    mat_df['_key'] = mat_df['原料名_orig'].apply(normalize_name)
    
    key_to_jan = {}
    for _, r in mat_df.iterrows():
        k = r['_key']
        if k and k not in key_to_jan:
            key_to_jan[k] = r['原料JAN']
    
    rec_df = rec_df.copy()
    rec_df['JAN'] = rec_df['JAN'].str.strip()
    rec_df['原料JAN'] = rec_df['原料JAN'].str.strip()
    rec_df['商品名_orig'] = rec_df['商品名'].astype(str).str.strip()
    rec_df['原料名_orig'] = rec_df['原料名'].astype(str).str.strip()
    rec_df['_pkey'] = rec_df['商品名_orig'].apply(normalize_name)
    
    prod_key_to_jan = {}
    for _, r in rec_df.iterrows():
        k = r['_pkey']
        if k and k not in prod_key_to_jan:
            prod_key_to_jan[k] = r['JAN']
    
    fake_mat_counter = [0]
    fake_prod_counter = [0]
    fake_mat_assigned = {}
    fake_prod_assigned = {}
    
    def get_mat_jan(name):
        k = normalize_name(name)
        if not k:
            return None
        if k in key_to_jan:
            return key_to_jan[k]
        if k in fake_mat_assigned:
            return fake_mat_assigned[k]
        fake_mat_counter[0] += 1
        new_jan = f"9991{fake_mat_counter[0]:09d}"
        fake_mat_assigned[k] = new_jan
        return new_jan
    
    def get_prod_jan(name):
        k = normalize_name(name)
        if not k:
            return None
        if k in prod_key_to_jan:
            return prod_key_to_jan[k]
        if k in fake_prod_assigned:
            return fake_prod_assigned[k]
        fake_prod_counter[0] += 1
        new_jan = f"9992{fake_prod_counter[0]:09d}"
        fake_prod_assigned[k] = new_jan
        return new_jan
    
    return get_mat_jan, get_prod_jan, fake_mat_counter, fake_prod_counter


def calc_match_rate(df):
    """JAN列を「本物 / 仮JAN」に分類してカウント。

    判定方針：仮JANは生成時に '9991'（原料）/'9992'（商品）prefix で発番される。
    したがって「9991/9992 で始まらない＝本物」と否定形で判定する。
    こうすることで実データのprefix分布（2828, 2543, 2529...等）を逐一
    リスト管理する必要がなくなり、prefix追加の管理コストが消える。
    """
    if len(df) == 0:
        return 0, 0
    jans = df['JAN'].astype(str)
    fake = jans.str.startswith(('9991', '9992')).sum()
    return len(df) - fake, fake


# ========== フルロード処理 ==========
def process_full_load():
    """7ファイル全部を取り込んでpklを完全に作り直す"""
    print_section("Step 1: マスタ読込（フルロード）")
    
    mat = read_csv_safely(RAW_DIR / '原料データ.csv')
    rec = read_csv_safely(RAW_DIR / 'レシピデータ.csv')
    print(f"  原料マスタ: {len(mat)}件")
    print(f"  レシピ: {len(rec)}行")
    
    get_mat_jan, get_prod_jan, fmc, fpc = build_jan_resolvers(mat, rec)
    
    OUT_DIR.mkdir(exist_ok=True)
    
    print_section("Step 2: 各ファイル加工")
    
    # 原料データ：マスタはそのまま
    mat['原料名'] = mat['原料名'].astype(str).str.strip()
    mat['原料JAN'] = mat['原料JAN'].str.strip()
    mat = _strip_all(mat)
    drop_cols = [c for c in mat.columns if c.startswith('_') or c in ['原料名_orig']]
    mat_out = mat.drop(columns=drop_cols, errors='ignore')
    mat_out.to_csv(OUT_DIR / '原料データ.csv', index=False, encoding='utf-8-sig')
    print(f"  [OK] 原料データ.csv: {len(mat_out)}行")
    
    # レシピ
    rec['JAN'] = rec['JAN'].str.strip()
    rec['原料JAN'] = rec['原料JAN'].str.strip()
    rec = _strip_all(rec)
    drop_cols = [c for c in rec.columns if c.startswith('_') or c in ['原料名_orig', '商品名_orig']]
    rec_out = rec.drop(columns=drop_cols, errors='ignore')
    rec_out.to_csv(OUT_DIR / 'レシピデータ.csv', index=False, encoding='utf-8-sig')
    print(f"  [OK] レシピデータ.csv: {len(rec_out)}行")
    
    # 棚卸
    inv = read_csv_safely(RAW_DIR / '棚卸データ.csv')
    if '店舗CD' in inv.columns and 'CD' not in inv.columns:
        inv = inv.rename(columns={'店舗CD': 'CD'})
    inv['原料名'] = inv['原料名'].astype(str).str.strip()
    # JAN列が健全（13桁数字）ならそのまま使う、無効行のみ原料名で応急処置
    if '原料JAN' in inv.columns:
        inv['JAN'] = inv['原料JAN'].astype(str).str.strip()
        mask_invalid = ~inv['JAN'].str.fullmatch(r'\d{13}', na=False)
        if mask_invalid.any():
            inv.loc[mask_invalid, 'JAN'] = inv.loc[mask_invalid, '原料名'].apply(get_mat_jan)
    else:
        inv['JAN'] = inv['原料名'].apply(get_mat_jan)
    inv = _strip_all(inv)
    inv.to_csv(OUT_DIR / '棚卸データ.csv', index=False, encoding='utf-8-sig')
    real, fake = calc_match_rate(inv)
    print(f"  [OK] 棚卸データ.csv: {len(inv)}行 (実{real}/仮{fake})")
    
    # 計画数
    plan = read_csv_safely(RAW_DIR / '計画数データ.csv')
    plan['商品名'] = plan['商品名'].astype(str).str.strip()
    # JAN列が健全（13桁数字）ならそのまま使う、無効行のみ商品名で応急処置
    if 'JAN' in plan.columns:
        plan['JAN'] = plan['JAN'].astype(str).str.strip()
        mask_invalid = ~plan['JAN'].str.fullmatch(r'\d{13}', na=False)
        if mask_invalid.any():
            plan.loc[mask_invalid, 'JAN'] = plan.loc[mask_invalid, '商品名'].apply(get_prod_jan)
    else:
        plan['JAN'] = plan['商品名'].apply(get_prod_jan)
    plan = _strip_all(plan)
    plan.to_csv(OUT_DIR / '計画数データ.csv', index=False, encoding='utf-8-sig')
    real, fake = calc_match_rate(plan)
    print(f"  [OK] 計画数データ.csv: {len(plan)}行 (実{real}/仮{fake})")
    
    # 製造数
    aprod = read_csv_safely(RAW_DIR / '製造数データ.csv')
    aprod['商品名'] = aprod['商品名'].astype(str).str.strip()
    # JAN列が健全（13桁数字）ならそのまま使う、無効行のみ商品名で応急処置
    if 'JAN' in aprod.columns:
        aprod['JAN'] = aprod['JAN'].astype(str).str.strip()
        mask_invalid = ~aprod['JAN'].str.fullmatch(r'\d{13}', na=False)
        if mask_invalid.any():
            aprod.loc[mask_invalid, 'JAN'] = aprod.loc[mask_invalid, '商品名'].apply(get_prod_jan)
    else:
        aprod['JAN'] = aprod['商品名'].apply(get_prod_jan)
    aprod = _strip_all(aprod)
    aprod.to_csv(OUT_DIR / '製造数データ.csv', index=False, encoding='utf-8-sig')
    real, fake = calc_match_rate(aprod)
    print(f"  [OK] 製造数データ.csv: {len(aprod)}行 (実{real}/仮{fake})")
    
    # 納品予定数
    pdel = read_csv_safely(RAW_DIR / '納品予定数.csv')
    if '日付' in pdel.columns and '納品予定日' not in pdel.columns:
        pdel = pdel.rename(columns={'日付': '納品予定日'})
    pdel['原料名'] = pdel['原料名'].astype(str).str.strip()
    # JAN列が健全（13桁数字）ならそのまま使う、無効行のみ原料名で応急処置
    if '原料JAN' in pdel.columns:
        pdel['JAN'] = pdel['原料JAN'].astype(str).str.strip()
        mask_invalid = ~pdel['JAN'].str.fullmatch(r'\d{13}', na=False)
        if mask_invalid.any():
            pdel.loc[mask_invalid, 'JAN'] = pdel.loc[mask_invalid, '原料名'].apply(get_mat_jan)
    else:
        pdel['JAN'] = pdel['原料名'].apply(get_mat_jan)
    pdel = _strip_all(pdel)
    pdel, _ = convert_houzai_units(pdel, 'plan_del', mat)   # [Phase2] 包材 ボウル→枚
    pdel.to_csv(OUT_DIR / '納品予定数.csv', index=False, encoding='utf-8-sig')
    real, fake = calc_match_rate(pdel)
    print(f"  [OK] 納品予定数.csv: {len(pdel)}行 (実{real}/仮{fake})")
    
    # 実納品数
    adel = read_csv_safely(RAW_DIR / '実納品数データ.csv')
    if '日付' in adel.columns and '納品予定日' not in adel.columns:
        adel = adel.rename(columns={'日付': '納品予定日'})
    adel['原料名'] = adel['原料名'].astype(str).str.strip()
    # JAN列が健全（13桁数字）ならそのまま使う、無効行のみ原料名で応急処置
    if '原料JAN' in adel.columns:
        adel['JAN'] = adel['原料JAN'].astype(str).str.strip()
        mask_invalid = ~adel['JAN'].str.fullmatch(r'\d{13}', na=False)
        if mask_invalid.any():
            adel.loc[mask_invalid, 'JAN'] = adel.loc[mask_invalid, '原料名'].apply(get_mat_jan)
    else:
        adel['JAN'] = adel['原料名'].apply(get_mat_jan)
    adel = _strip_all(adel)
    adel, _ = convert_houzai_units(adel, 'actual_del', mat)  # [Phase2] 包材 ボウル→枚
    adel.to_csv(OUT_DIR / '実納品数データ.csv', index=False, encoding='utf-8-sig')
    real, fake = calc_match_rate(adel)
    print(f"  [OK] 実納品数データ.csv: {len(adel)}行 (実{real}/仮{fake})")
    
    print_section("Step 3: session_data.pkl を更新")
    
    loaded = {
        'material': mat_out,
        'recipe': rec_out,
        'inventory': inv,
        'plan': plan,
        'actual_prod': aprod,
        'plan_del': pdel,
        'actual_del': adel,
    }
    
    save_loaded_to_pkl(loaded)
    
    print(f"\n  仮JAN生成: 原料 {fmc[0]}件 / 商品 {fpc[0]}件")
    return True


# ========== 差分追加処理 ==========
def process_diff_load(diff_files):
    """既存pklを読んで、差分ファイルでマージしてpklを保存"""
    print_section("Step 1: 既存データを読込")
    
    if not SAVE_PATH.exists():
        print(f"  [ERROR] 既存のpklがありません: {SAVE_PATH}")
        print(f"  差分追加するには、先にフルロードを行ってください。")
        print(f"  input_raw\\ に基本7ファイルを置いて update_data.bat を実行してください。")
        return False
    
    with open(SAVE_PATH, 'rb') as f:
        existing = pickle.load(f)
    loaded = existing['loaded']
    print(f"  [OK] 既存pkl読込: {len(loaded.get('inventory', []))}行（棚卸）")

    # 既存pkl 内の重複列を自動削除（2026-05-13 追加：merge_diff 中の Reindexing エラー対策）
    for k in ['actual_prod', 'actual_del', 'plan', 'plan_del', 'inventory']:
        if k in loaded and any(list(loaded[k].columns).count(c) > 1 for c in loaded[k].columns):
            dup_set = {c for c in loaded[k].columns if list(loaded[k].columns).count(c) > 1}
            print(f"  [INFO] {k}: 既存pkl 重複列を自動削除 {dup_set}")
            loaded[k] = loaded[k].loc[:, ~loaded[k].columns.duplicated()]
    
    # 既存マスタからJAN解決器を作成
    get_mat_jan, get_prod_jan, fmc, fpc = build_jan_resolvers(
        loaded['material'], loaded['recipe']
    )
    
    print_section("Step 2: 差分ファイルを処理")

    OUT_DIR.mkdir(exist_ok=True)

    # 運用店舗リスト（棚卸データの店舗 = pkl の stores と同じ正準ソース）。
    # 差分ファイルは全国全店舗を含むため、運用店舗だけに絞る
    # (2026-05-30 追加：9店舗運用なのに全店舗が差分流入する不具合を修正＝バグ2)
    target_stores = set()
    if 'inventory' in loaded and '店舗名' in loaded['inventory'].columns:
        target_stores = set(loaded['inventory']['店舗名'].dropna().unique())
    if target_stores:
        print(f"  運用店舗で絞り込み: {len(target_stores)}店舗")

    summary_lines = []
    for f, date_str, kind in diff_files:
        print(f"\n  ファイル: {f.name}（{date_str}・{kind}）")

        new_df = read_csv_safely(f)

        # 運用店舗フィルタ（店舗名がある種別のみ）
        if target_stores and '店舗名' in new_df.columns:
            _before = len(new_df)
            new_df = new_df[new_df['店舗名'].isin(target_stores)].copy()
            _dropped = _before - len(new_df)
            if _dropped > 0:
                print(f"    運用外店舗を除外: {_dropped}行 → 残り{len(new_df)}行")

        # JAN列の品質判定して、健全なら使う、壊れていれば応急処置（フルロード経路と同じパターン）
        if kind == 'inventory':
            if '店舗CD' in new_df.columns and 'CD' not in new_df.columns:
                new_df = new_df.rename(columns={'店舗CD': 'CD'})
            new_df['原料名'] = new_df['原料名'].astype(str).str.strip()
            if '原料JAN' in new_df.columns:
                new_df['JAN'] = new_df['原料JAN'].astype(str).str.strip()
                mask_invalid = ~new_df['JAN'].str.fullmatch(r'\d{13}', na=False)
                if mask_invalid.any():
                    new_df.loc[mask_invalid, 'JAN'] = new_df.loc[mask_invalid, '原料名'].apply(get_mat_jan)
            else:
                new_df['JAN'] = new_df['原料名'].apply(get_mat_jan)
            new_df = _strip_all(new_df)
        elif kind == 'actual_prod':
            new_df['商品名'] = new_df['商品名'].astype(str).str.strip()
            if 'JAN' in new_df.columns:
                new_df['JAN'] = new_df['JAN'].astype(str).str.strip()
                mask_invalid = ~new_df['JAN'].str.fullmatch(r'\d{13}', na=False)
                if mask_invalid.any():
                    new_df.loc[mask_invalid, 'JAN'] = new_df.loc[mask_invalid, '商品名'].apply(get_prod_jan)
            else:
                new_df['JAN'] = new_df['商品名'].apply(get_prod_jan)
            new_df = _strip_all(new_df)
        elif kind == 'actual_del':
            # 日付→納品予定日 列名統一（フルロード経路と揃える）
            if '日付' in new_df.columns and '納品予定日' not in new_df.columns:
                new_df = new_df.rename(columns={'日付': '納品予定日'})
            # 列名フォールバック（2026-05-30 追加：convert 出力の列名ズレでも落ちないように＝バグ1保険）
            if '原料名' not in new_df.columns and '商品名' in new_df.columns:
                new_df['原料名'] = new_df['商品名']
            if '原料JAN' not in new_df.columns and 'JAN' in new_df.columns:
                new_df['原料JAN'] = new_df['JAN']
            new_df['原料名'] = new_df['原料名'].astype(str).str.strip()
            if '原料JAN' in new_df.columns:
                new_df['JAN'] = new_df['原料JAN'].astype(str).str.strip()
                mask_invalid = ~new_df['JAN'].str.fullmatch(r'\d{13}', na=False)
                if mask_invalid.any():
                    new_df.loc[mask_invalid, 'JAN'] = new_df.loc[mask_invalid, '原料名'].apply(get_mat_jan)
            else:
                new_df['JAN'] = new_df['原料名'].apply(get_mat_jan)
            new_df = _strip_all(new_df)
            new_df, _ = convert_houzai_units(new_df, 'actual_del', loaded['material'])  # [Phase2]
        elif kind == 'plan_del':
            # 2026/5/18 追加：納品予定数の差分追加経路（actual_del と同じ処理）
            if '日付' in new_df.columns and '納品予定日' not in new_df.columns:
                new_df = new_df.rename(columns={'日付': '納品予定日'})
            # 列名フォールバック（2026-05-30 追加：convert 出力の列名ズレでも落ちないように＝バグ1保険）
            if '原料名' not in new_df.columns and '商品名' in new_df.columns:
                new_df['原料名'] = new_df['商品名']
            if '原料JAN' not in new_df.columns and 'JAN' in new_df.columns:
                new_df['原料JAN'] = new_df['JAN']
            new_df['原料名'] = new_df['原料名'].astype(str).str.strip()
            if '原料JAN' in new_df.columns:
                new_df['JAN'] = new_df['原料JAN'].astype(str).str.strip()
                mask_invalid = ~new_df['JAN'].str.fullmatch(r'\d{13}', na=False)
                if mask_invalid.any():
                    new_df.loc[mask_invalid, 'JAN'] = new_df.loc[mask_invalid, '原料名'].apply(get_mat_jan)
            else:
                new_df['JAN'] = new_df['原料名'].apply(get_mat_jan)
            new_df = _strip_all(new_df)
            new_df, _ = convert_houzai_units(new_df, 'plan_del', loaded['material'])  # [Phase2]

        # 既存データとマージ（同じキーがあれば上書き）
        existing_df = loaded.get(kind, pd.DataFrame())
        merged_df, n_replaced, n_added = merge_diff(existing_df, new_df, kind)
        loaded[kind] = merged_df
        
        # 加工済みCSVも出力（参考）
        new_df.to_csv(OUT_DIR / f.name, index=False, encoding='utf-8-sig')
        
        msg = f"    {len(new_df)}行を取込 → 上書き{n_replaced}件 / 新規{n_added}件 / 統合後{len(merged_df)}行"
        print(msg)
        summary_lines.append(f"  {f.name}: {msg.strip()}")
    
    print_section("Step 3: session_data.pkl を保存")
    save_loaded_to_pkl(loaded)
    
    print(f"\n  仮JAN生成: 原料 {fmc[0]}件 / 商品 {fpc[0]}件")
    return True


def merge_diff(existing_df, new_df, kind):
    """既存DataFrameと新DataFrameをマージ。同じキーがあれば新を採用。"""
    if len(existing_df) == 0:
        return new_df.copy(), 0, len(new_df)
    
    # キーを決定（kind ごと）
    if kind == 'inventory':
        key_cols = ['店舗', '原料名', '棚卸日']  # 店舗名がない場合のフォールバック
        if '店舗名' in existing_df.columns:
            key_cols[0] = '店舗名'
        if '店舗名' in new_df.columns and '店舗名' not in key_cols:
            key_cols[0] = '店舗名'
    elif kind == 'actual_prod':
        key_cols = ['店舗名', '商品名', '日付']
        if '店舗名' not in existing_df.columns and '店CD' in existing_df.columns:
            key_cols[0] = '店CD'
    elif kind == 'actual_del':
        key_cols = ['店舗名', '原料名', '納品予定日']
    elif kind == 'plan_del':
        key_cols = ['店舗名', '原料名', '納品予定日']  # 2026/5/18 追加
    else:
        # 不明な種別：単純連結
        return pd.concat([existing_df, new_df], ignore_index=True), 0, len(new_df)
    
    # 既存DFと新DFで列名を合わせる（店舗名のリネーム後の状態を考慮）
    # 既存DFは process_uploaded_files で 店舗→店舗名 に変換済みの可能性が高い
    
    # キーが両方のDFに存在するか確認
    missing_in_existing = [c for c in key_cols if c not in existing_df.columns]
    missing_in_new = [c for c in key_cols if c not in new_df.columns]
    
    if missing_in_existing or missing_in_new:
        print(f"    [WARN] キー列の不一致: existing欠損={missing_in_existing}, new欠損={missing_in_new}")
        # フォールバック：単純追加
        return pd.concat([existing_df, new_df], ignore_index=True), 0, len(new_df)
    
    # 日付列はフォーマット差（"2026/5/15" vs "2026/05/15"）で文字列キーが不一致になり、
    # 旧行が置換されず二重計上を生む。パースして正規化（ISO日付）してから比較する。
    # (2026-05-25 追加：plan_del 差分が月初フルロードの非ゼロ埋め日付と一致せず、
    #  旧行が残って二重計上した不具合への対策。actual_del/prod 等は両側ゼロ埋め同士なので
    #  挙動は変わらず、堅牢性のみ向上する)
    date_key_cols = {'納品予定日', '日付', '棚卸日'}

    def _row_key(row):
        parts = []
        for c in key_cols:
            if c in date_key_cols:
                d = parse_jp_date(row[c])
                parts.append(d.isoformat() if d else str(row[c]).strip())
            else:
                parts.append(str(row[c]).strip())
        return tuple(parts)

    # 新DFのキーセットを作る（日付正規化済み）
    new_keys = set(_row_key(r) for _, r in new_df.iterrows())

    # 既存から、新DFと同じキーの行を除外
    def is_replaced(row):
        return _row_key(row) in new_keys
    
    existing_mask = existing_df.apply(is_replaced, axis=1)
    n_replaced = existing_mask.sum()
    existing_kept = existing_df[~existing_mask]
    
    # 連結
    merged = pd.concat([existing_kept, new_df], ignore_index=True)
    n_added = len(new_df) - n_replaced
    
    return merged, n_replaced, n_added


# ========== pkl保存 ==========
def save_loaded_to_pkl(loaded):
    """loaded辞書をsession_data.pklに保存（列名統一・日付パースも実施）"""
    # 列名統一
    if 'inventory' in loaded and '店舗' in loaded['inventory'].columns:
        loaded['inventory'] = loaded['inventory'].rename(columns={'店舗': '店舗名', 'CD': '店舗CD'})
    if 'plan' in loaded and '店舗' in loaded['plan'].columns:
        loaded['plan'] = loaded['plan'].rename(columns={'店舗': '店舗名', 'CD': '店舗CD'})
    if 'actual_prod' in loaded and '店CD' in loaded['actual_prod'].columns:
        aprod = loaded['actual_prod']
        if '店舗CD' in aprod.columns:
            # 既存pkl(列=店舗CD)＋新CSV(列=店CD)を concat すると両列が共存し、
            # 新行は店舗CDがNaN/店CDに値、既存行は逆になる。単純 drop すると新行の
            # 店舗CDがNaNのまま残る(2026-06-11 店舗CD欠落バグの真因)。
            # → drop前に、店舗CDがNaNの行だけ店CDで埋める(coalesce)。重複列を作らない元意図は維持。
            aprod['店舗CD'] = aprod['店舗CD'].fillna(aprod['店CD'])  # (2026-06-11 修正)
            loaded['actual_prod'] = aprod.drop(columns=['店CD'])
        else:
            loaded['actual_prod'] = aprod.rename(columns={'店CD': '店舗CD'})

    # 重複列の自動削除（万一の保険、2026-05-13 追加）
    for k in ['actual_prod', 'actual_del', 'plan', 'plan_del', 'inventory']:
        if k in loaded and any(list(loaded[k].columns).count(c) > 1 for c in loaded[k].columns):
            print(f"  [WARN] {k}: 重複列を自動削除")
            loaded[k] = loaded[k].loc[:, ~loaded[k].columns.duplicated()]

    # 完全な空行の除去（主要列が全て空の行）。
    # 業務システムの製造数CSV等に紛れる空行（日付・店舗・商品・数量がすべて空）は、
    # 日付_d を None にして集計（例：actual_prod['日付_d'].max()）を TypeError で壊す。
    # フル/差分どちらの経路もここを通るので、保存直前に各テーブルから捨てる。
    # (2026-05-25 追加：actual_prod に419件の空行が混入し画面が落ちた件の根本対策)
    blank_check_cols = {
        'actual_prod': ['日付', '店舗名', '商品名', '製造数'],
        'actual_del':  ['納品予定日', '店舗名', '原料名', '仕入計上数'],
        'plan_del':    ['納品予定日', '店舗名', '原料名', '納品予定数'],
        'plan':        ['日付', '店舗名', '商品名', '計画数'],
        'inventory':   ['棚卸日', '店舗名', '原料名', '棚卸数'],
    }
    for k, cols in blank_check_cols.items():
        if k not in loaded:
            continue
        df = loaded[k]
        cols = [c for c in cols if c in df.columns]
        if not cols:
            continue
        blank = df[cols].apply(
            lambda s: s.isna() | s.astype(str).str.strip().isin(['', 'nan', 'None', 'NaN'])
        ).all(axis=1)
        n = int(blank.sum())
        if n:
            print(f"  [INFO] {k}: 主要列が全て空の行 {n}件を除去")
            loaded[k] = df[~blank].reset_index(drop=True)

    # 完全同一行（全列一致）の重複除去 ― 計画・予定・棚卸スナップショット系のみ。
    # 背景：plan(計画数) に source 由来の byte 同一重複行が混入し、calculate_flow が
    #   (商品JAN×日付) で合算(+=)するため計画使用量が二重計上 → 計画在庫推移／計画余裕日数が
    #   過大に出ていた（2026-06-08 検出：plan 797行・計画数 +12% 膨張）。
    #   inventory は groupby('JAN')['棚卸数'].sum() で期首在庫が、plan_del は納品予定が同様に二重化しうる。
    # 安全弁：実績系(actual_prod/actual_del)は「同数量の別イベント」が正規にあり得るため対象外。
    #   ここで落とすのは全列一致の冗長行のみ（別数量・別商品名の複数行は保持＝合算が正しい）。
    for k in ['plan', 'plan_del', 'inventory']:
        if k not in loaded:
            continue
        df = loaded[k]
        before = len(df)
        df2 = df.drop_duplicates().reset_index(drop=True)
        removed = before - len(df2)
        if removed:
            print(f"  [INFO] {k}: 全列一致の重複行 {removed}件を除去（二重計上防止）")
            loaded[k] = df2

    # 日付パース
    if 'inventory' in loaded:
        loaded['inventory']['棚卸日_d'] = loaded['inventory']['棚卸日'].apply(parse_jp_date)
    if 'plan' in loaded:
        loaded['plan']['日付_d'] = loaded['plan']['日付'].apply(parse_jp_date)
    if 'actual_prod' in loaded:
        loaded['actual_prod']['日付_d'] = loaded['actual_prod']['日付'].apply(parse_jp_date)
    if 'plan_del' in loaded:
        loaded['plan_del']['納品予定日_d'] = loaded['plan_del']['納品予定日'].apply(parse_jp_date)
    if 'actual_del' in loaded:
        loaded['actual_del']['納品予定日_d'] = loaded['actual_del']['納品予定日'].apply(parse_jp_date)

    # ------------------------------------------------------------------
    # 正規化キーの重複除去（2026-08-06 追加）― フルロード×差分の接合面の二重計上
    #
    # 背景：上の「全列一致」dedup(§P) は **日付パースより前** に走るため、同じ入荷が
    #   表記ゆれで別行として生き残っていた。実物（粕屋CK・タレ）：
    #     2841000126967 煮詰めのタレ６００ｇ        2026/7/30  7   ← 差分由来
    #     2841000126967 煮詰めのタレ６００ｇ「ＣＫ用」2026/07/30 7   ← フルロード由来
    #   ゆれは2種類：①日付のゼロ埋め有無（2026/7/30 と 2026/07/30）
    #                ②原料名の版差（「ＣＫ用」の有無）
    #   → 全列一致にならず素通り。しかし 納品予定日_d に正規化すると同一日なので、
    #     calculate_flow の pdel_dict / adel_dict が (JAN,日付) で += して **2倍** になる。
    #   規模（2026-08-06 実測）：actual_del 631グループ/1319行（うち数量同一594）、
    #                            plan_del 919グループ/1838行（うち数量同一811）。
    #   実害：煮詰めのタレ +11本 / TR漬け丼のたれ +12本（7月・粕屋CK）。
    #   ＝2026-05-25 修正の C5「merge_diff 日付キー未正規化→二重計上」と同型の再発。
    #     当時は差分内、今回はフルロード×差分の接合面。
    #
    # 安全弁：落とすのは **キー(店舗名×JAN×日付_d) と数量まで一致** する行だけ。
    #   数量が異なるキー重複は「分割計上・別イベント」があり得るので **残す**（合算が正しい）。
    #   ＝§P の思想（全列一致の冗長行のみ落とす）を、日付正規化後のキーへ拡張したもの。
    #   原料名／商品名の版差は比較に含めない（版差こそが今回のゆれの正体のため）。
    # ------------------------------------------------------------------
    KEY_DEDUP_SPEC = {
        'plan_del':    (['店舗名', 'JAN', '納品予定日_d'], '納品予定数'),
        'actual_del':  (['店舗名', 'JAN', '納品予定日_d'], '仕入計上数'),
        'plan':        (['店舗名', 'JAN', '日付_d'],       '計画数'),
        'actual_prod': (['店舗名', 'JAN', '日付_d'],       '製造数'),
    }
    for k, (key_cols, qty_col) in KEY_DEDUP_SPEC.items():
        if k not in loaded:
            continue
        df = loaded[k]
        if not all(c in df.columns for c in key_cols) or qty_col not in df.columns:
            continue
        before = len(df)
        # 数量は文字列で保持されているため、比較用に数値へ正規化した一時列を使う
        qty_norm = pd.to_numeric(df[qty_col], errors='coerce')
        subset_df = df[key_cols].copy()
        subset_df['_qty_norm'] = qty_norm
        keep = ~subset_df.duplicated(keep='first')
        removed = int((~keep).sum())
        if removed:
            loaded[k] = df[keep].reset_index(drop=True)
            print(f"  [INFO] {k}: 正規化キー＋数量が一致する重複行 {removed}件を除去"
                  f"（表記ゆれ由来の二重計上防止・{before}→{len(loaded[k])}）")
        # 数量が異なるキー重複は落とさない。ただし黙って合算されるので件数だけ申告する。
        rest = loaded[k]
        dup_keys = rest.duplicated(subset=key_cols, keep=False)
        n_rest = int(dup_keys.sum())
        if n_rest:
            print(f"  [WARN] {k}: 数量が異なるキー重複 {n_rest}行は保持（合算が正しい可能性）。"
                  f"ソース側の確認対象。")

    last_update = datetime.datetime.now()
    stores = sorted(loaded['inventory']['店舗名'].dropna().unique().tolist()) if 'inventory' in loaded else []
    
    DATA_DIR.mkdir(exist_ok=True)
    with open(SAVE_PATH, 'wb') as f:
        pickle.dump({
            'loaded': loaded,
            'last_update': last_update,
            'stores': stores,
        }, f)
    
    print(f"  [OK] {SAVE_PATH}")
    store_show = ', '.join(stores[:5]) + ('...' if len(stores) > 5 else '')
    print(f"       店舗数: {len(stores)} ({store_show})")


# ========== メイン ==========
def main():
    start_time = datetime.datetime.now()
    print_section("ZAIKO KANRI: data update")
    print(f"  実行時刻: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  入力フォルダ: {RAW_DIR}")
    
    # モード判定
    mode, payload = detect_mode()
    
    if mode == 'no_folder':
        print(f"\n[ERROR] フォルダがありません: {RAW_DIR}")
        print(f"このフォルダを作成して、CSVファイルを置いてください。")
        return False
    
    if mode == 'empty':
        print(f"\n[ERROR] {RAW_DIR} にCSVファイルがありません。")
        print(f"\n使い方:")
        print(f"  ・フルロード（月初）：基本7ファイルを置く")
        print(f"  ・差分追加（日次）：YYYYMMDD<種別>.csv を置く")
        return False
    
    if mode == 'invalid':
        print(f"\n[ERROR] フォルダの中身が判定できません: {payload}")
        print(f"\n以下のいずれかの形にしてください:")
        print(f"\n  (A) フルロード - 基本7ファイル全部を置く:")
        for n in FULL_FILES:
            print(f"      {n}")
        print(f"\n  (B) 差分追加 - 日付付きファイル名にする:")
        print(f"      20260507製造数.csv")
        print(f"      20260507実納品数.csv")
        print(f"      20260507棚卸.csv")
        return False
    
    if mode == 'mixed':
        print(f"\n[ERROR] フルロードと差分追加のファイルが混在しています。")
        print(f"どちらか一方にしてください。")
        return False
    
    # 実行
    if mode == 'full':
        print(f"\n  モード: フルロード（基本7ファイル）")
        ok = process_full_load()
    elif mode == 'diff':
        print(f"\n  モード: 差分追加（{len(payload)}ファイル）")
        for f, date_str, kind in payload:
            print(f"    - {f.name} （{date_str}・{kind}）")
        ok = process_diff_load(payload)
    else:
        print(f"\n[ERROR] 不明なモード: {mode}")
        return False
    
    if not ok:
        return False
    
    elapsed = (datetime.datetime.now() - start_time).total_seconds()
    print_section("完了")
    print(f"  処理時間: {elapsed:.1f}秒")
    print(f"\n  次の手順:")
    print(f"    1. アプリ起動中の場合 → 一旦停止 (Ctrl+C)")
    print(f"    2. start_app.bat をダブルクリック")
    print(f"    3. ブラウザで最新データを確認")
    
    return True


if __name__ == '__main__':
    try:
        ok = main()
        if ok:
            print("\n[OK] 処理完了\n")
        else:
            print("\n[NG] 処理失敗\n")
    except Exception as e:
        import traceback
        print(f"\n[ERROR] {e}\n")
        traceback.print_exc()
    finally:
        input("\nEnter キーで終了...")
