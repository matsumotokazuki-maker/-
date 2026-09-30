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
    
    summary_lines = []
    for f, date_str, kind in diff_files:
        print(f"\n  ファイル: {f.name}（{date_str}・{kind}）")
        
        new_df = read_csv_safely(f)
        
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
            new_df['原料名'] = new_df['原料名'].astype(str).str.strip()
            if '原料JAN' in new_df.columns:
                new_df['JAN'] = new_df['原料JAN'].astype(str).str.strip()
                mask_invalid = ~new_df['JAN'].str.fullmatch(r'\d{13}', na=False)
                if mask_invalid.any():
                    new_df.loc[mask_invalid, 'JAN'] = new_df.loc[mask_invalid, '原料名'].apply(get_mat_jan)
            else:
                new_df['JAN'] = new_df['原料名'].apply(get_mat_jan)
            new_df = _strip_all(new_df)
        elif kind == 'plan_del':
            # 2026/5/18 追加：納品予定数の差分追加経路（actual_del と同じ処理）
            if '日付' in new_df.columns and '納品予定日' not in new_df.columns:
                new_df = new_df.rename(columns={'日付': '納品予定日'})
            new_df['原料名'] = new_df['原料名'].astype(str).str.strip()
            if '原料JAN' in new_df.columns:
                new_df['JAN'] = new_df['原料JAN'].astype(str).str.strip()
                mask_invalid = ~new_df['JAN'].str.fullmatch(r'\d{13}', na=False)
                if mask_invalid.any():
                    new_df.loc[mask_invalid, 'JAN'] = new_df.loc[mask_invalid, '原料名'].apply(get_mat_jan)
            else:
                new_df['JAN'] = new_df['原料名'].apply(get_mat_jan)
            new_df = _strip_all(new_df)

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
    
    # 新DFのキーセットを作る
    new_keys = set()
    for _, r in new_df.iterrows():
        key = tuple(str(r[c]).strip() for c in key_cols)
        new_keys.add(key)
    
    # 既存から、新DFと同じキーの行を除外
    def is_replaced(row):
        key = tuple(str(row[c]).strip() for c in key_cols)
        return key in new_keys
    
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
            # 既に「店舗CD」がある場合、リネームすると重複するので「店CD」を削除 (2026-05-13 修正)
            loaded['actual_prod'] = aprod.drop(columns=['店CD'])
        else:
            loaded['actual_prod'] = aprod.rename(columns={'店CD': '店舗CD'})

    # 重複列の自動削除（万一の保険、2026-05-13 追加）
    for k in ['actual_prod', 'actual_del', 'plan', 'plan_del', 'inventory']:
        if k in loaded and any(list(loaded[k].columns).count(c) > 1 for c in loaded[k].columns):
            print(f"  [WARN] {k}: 重複列を自動削除")
            loaded[k] = loaded[k].loc[:, ~loaded[k].columns.duplicated()]
    
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
