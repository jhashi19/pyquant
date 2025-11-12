PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

/********** 0) 列挙マスタ **********/
CREATE TABLE m_interp_method (
  interp_method TEXT PRIMARY KEY  -- 'LOG_LINEAR_DF','LINEAR_ZERO','PIECEWISE_CONST_FWD','CUBIC_SPLINE_ZERO'
);
CREATE TABLE m_extrap_method (
  extrap_method TEXT PRIMARY KEY  -- 'FLAT_FWD','FLAT_ZERO','LINEAR_ZERO'
);
CREATE TABLE m_trade_product (
  product TEXT PRIMARY KEY  -- 'IRS','BOND_FIXED','BOND_FLOAT','BOND_ZC','FXFWD','FXOPT_EU','CAPFLOOR','SWAPTION_EU'
);

/********** 1) 参照データ **********/
CREATE TABLE currency (
  ccy TEXT PRIMARY KEY CHECK(length(ccy)=3),
  name TEXT NOT NULL,                   -- 表示名: 'Japanese Yen', 'US Dollar'
  iso_numeric INTEGER UNIQUE,           -- ISO4217 数値コード: JPY=392, USD=840
  minor_unit INTEGER NOT NULL,          -- 小数桁: JPY=0, USD=2
  symbol TEXT,                          -- '¥', '$'
  spot_lag INTEGER NOT NULL DEFAULT 2,  -- FXスポット決済ラグ（営業日）
  default_cal_id TEXT,                  -- 既定カレンダーID（例: 'JPTO','USNY')
  enabled INTEGER NOT NULL DEFAULT 1,   -- 1=有効, 0=無効
  valid_from TEXT NOT NULL,             -- 'YYYY-MM-DD'
  retired_at TEXT,                      -- 廃止日
  created_at TEXT NOT NULL              -- 作成UTC
);

CREATE TABLE currency_calendar ( -- 任意：複数暦の紐付け
  ccy TEXT NOT NULL REFERENCES currency(ccy),
  cal_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('DEFAULT','SETTLEMENT','HOLIDAY_ONLY')),
  PRIMARY KEY (ccy, cal_id, role)
);

CREATE TABLE daycount (
  code TEXT PRIMARY KEY,                         -- 'ACT/360','ACT/365F','ACT/ACT-ISDA','30E/360' 等
  display_name TEXT NOT NULL,                    -- 表示名
  formula_tag TEXT NOT NULL UNIQUE,              -- 実装識別子: 'ACT_360','ACT_365F','ACT_ACT_ISDA','THIRTY_E_360' 等
  params_json TEXT,                              -- 方式固有パラメータ（任意）。例: {"denom":360}
  notes TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);

CREATE TABLE bizday_convention (
  code TEXT PRIMARY KEY,                          -- 'F','P','MF','MP','MFM','NONE'
  display_name TEXT NOT NULL,                     -- 表示名
  rule_tag TEXT NOT NULL,                         -- 'FOLLOWING','PRECEDING','MOD_FOLLOWING','MOD_PRECEDING','NEAREST','NONE'
  nearest_tiebreaker TEXT CHECK(nearest_tiebreaker IN ('PREV','NEXT')), -- NEAREST専用
  notes TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);

CREATE TABLE calendar_def (
  cal_id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  time_zone TEXT NOT NULL,                 -- e.g., 'Asia/Tokyo'
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);

CREATE TABLE market_holiday (
  cal_id TEXT NOT NULL REFERENCES calendar_def(cal_id),
  holiday TEXT NOT NULL,            -- 'YYYY-MM-DD'
  holiday_name TEXT,
  holiday_type TEXT,                -- 'NATIONAL','BANK','MARKET','OBSERVED','AD_HOC'
  observed_of TEXT,                 -- 振替元 'YYYY-MM-DD'
  is_half_day INTEGER NOT NULL DEFAULT 0,
  notes TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  PRIMARY KEY (cal_id, holiday)
);

CREATE TABLE ref_rate_rule (
  index_id TEXT PRIMARY KEY,                        -- 'USD-SOFR','USD-SOFR-3M','JPY-TONAR' 等
  ccy TEXT NOT NULL REFERENCES currency(ccy),
  tenor TEXT NOT NULL,                              -- 'ON','1M','3M' 等
  daycount TEXT NOT NULL REFERENCES daycount(code),
  display_name TEXT NOT NULL,                       -- 表示名
  index_family TEXT NOT NULL,                       -- 'SOFR','TONAR','LIBOR' 等
  rate_type TEXT NOT NULL CHECK(rate_type IN ('ON','TERM')),
  fixing_cal_id TEXT NOT NULL,                      -- Fixing用カレンダー（例: 'USNY'）
  fixing_bdc TEXT NOT NULL REFERENCES bizday_convention(code),  -- 'F','MF' 等
  fixing_time_local TEXT,                           -- 'HH:MM'（任意）
  fixing_tz TEXT NOT NULL,                          -- 'America/New_York' 等
  publication_lag_days INTEGER NOT NULL DEFAULT 0,  -- 公表遅延（SOFR=1 等）
  accrual_conv TEXT NOT NULL CHECK(                 -- O/N→期間化規約
    accrual_conv IN ('SIMPLE','COMPOUND_IN_ARREARS','AVERAGE','TERM_QUOTE')
  ),
  lookback_days INTEGER NOT NULL DEFAULT 0,
  lockout_days INTEGER NOT NULL DEFAULT 0,
  source_tag TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  valid_from TEXT NOT NULL,
  retired_at TEXT,
  created_at TEXT NOT NULL
);

/********** 2) マーケット・スナップショット **********/
CREATE TABLE market_snapshot (
  snapshot_id         TEXT PRIMARY KEY,                        -- 例: UUID
  as_of               TEXT NOT NULL,                           -- 論理市場日 'YYYY-MM-DD'
  as_of_tz            TEXT NOT NULL DEFAULT 'Asia/Tokyo',      -- 市場日の解釈TZ（IANA）。起算日→現地日を決める
  cut_label           TEXT,                                     -- 'EOD','NY_10AM','LDN_4PM' 等の運用カット名
  data_hash           TEXT NOT NULL,                            -- 入力一式の総ハッシュ（再現性同定）
  parent_snapshot_id  TEXT REFERENCES market_snapshot(snapshot_id), -- 親版（差分や派生元）
  is_locked           INTEGER NOT NULL DEFAULT 1,               -- 1=ロック済（以後不変の想定）
  qa_status           TEXT CHECK(qa_status IN ('PENDING','APPROVED','REJECTED')), -- 品質審査状態
  note                TEXT,                                     -- 補足
  frozen_at           TEXT,                                     -- 当版を“凍結”したUTC時刻（EOD確定の実瞬間）
  created_at          TEXT NOT NULL                              -- 生成UTC
);
CREATE UNIQUE INDEX ux_market_snapshot_hash ON market_snapshot(data_hash);
CREATE INDEX        ix_market_snapshot_asof ON market_snapshot(as_of, cut_label);
CREATE INDEX        ix_market_snapshot_parent ON market_snapshot(parent_snapshot_id);

CREATE TABLE fx_spot (
  snapshot_id      TEXT NOT NULL REFERENCES market_snapshot(snapshot_id),
  base_ccy         TEXT NOT NULL REFERENCES currency(ccy),   -- レートの分子通貨（例: USD）
  quote_ccy        TEXT NOT NULL REFERENCES currency(ccy),   -- レートの分母通貨（例: JPY）
  pair             TEXT NOT NULL,                            -- 'USDJPY'
  /* base_ccy||quote_ccy と一致させて整合を保証 */
  CHECK (pair = base_ccy || quote_ccy),
  spot             REAL NOT NULL,                            -- MID（= (bid+ask)/2 を推奨）
  bid              REAL,                                     -- 片サイド（任意）
  ask              REAL,                                     -- 片サイド（任意）
  CHECK ( (bid IS NULL AND ask IS NULL) OR (bid <= ask) ),
  frozen_at   TEXT,                                     -- このペア固有の観測UTC時刻（任意）
  source_symbol    TEXT,                                     -- ベンダ銘柄/ティッカー（任意）

  /* 追加：クロス導出フラグ（ベンダ直物で無い場合を明示） */
  is_cross_derived INTEGER NOT NULL DEFAULT 0,               -- 1=クロス導出（例: EURJPY = EURUSD*USDJPY）
  derived_via_1    TEXT,                                     -- 由来ペア1（例: 'EURUSD'）
  derived_via_2    TEXT,                                     -- 由来ペア2（例: 'USDJPY'）
  /* クロス導出時のみ由来ペアを要求（運用上の一貫性チェック） */
  CHECK ( (is_cross_derived = 0) OR (derived_via_1 IS NOT NULL AND derived_via_2 IS NOT NULL) ),

  PRIMARY KEY (snapshot_id, pair),
  UNIQUE (snapshot_id, base_ccy, quote_ccy)
);
CREATE INDEX IF NOT EXISTS ix_fx_spot_ccys ON fx_spot(base_ccy, quote_ccy);

CREATE TABLE pricing_curve_def (
  curve_id      TEXT PRIMARY KEY,                                  -- 'JPY-OIS','USD-SOFR-3M' など一意名
  ccy           TEXT NOT NULL REFERENCES currency(ccy),            -- 曲線の通貨
  curve_type    TEXT NOT NULL CHECK (curve_type IN ('OIS','FORECAST','BOND')),

  /* FORECAST のときのみ対象参照金利を要求（例：'JPY-TONAR','USD-SOFR'） */
  ref_rate_id   TEXT,                                              -- NULL可（OIS/BOND）  
  CHECK ( (curve_type='FORECAST' AND ref_rate_id IS NOT NULL)
       OR (curve_type!='FORECAST' AND ref_rate_id IS NULL) ),

  /* 曲線“座標”の年率化と複利慣行（ゼロ↔DF 変換に使用） */
  daycount      TEXT NOT NULL REFERENCES daycount(code),
  compounding   TEXT NOT NULL DEFAULT 'CONT'
                CHECK (compounding IN ('CONT','SIMPLE','ANNUAL')),

  /* 評価規約：補間/外挿/ピラーの定義方法（実装を安定化） */
  interp_method TEXT NOT NULL REFERENCES m_interp_method(interp_method),
  extrap_left   TEXT NOT NULL DEFAULT 'FLAT_FWD'
                CHECK (extrap_left IN ('FLAT_FWD','FLAT_ZERO','LINEAR_ZERO')),
  extrap_right  TEXT NOT NULL DEFAULT 'FLAT_FWD'
                CHECK (extrap_right IN ('FLAT_FWD','FLAT_ZERO','LINEAR_ZERO')),
  pillar_mode   TEXT NOT NULL DEFAULT 'TENOR'
                CHECK (pillar_mode IN ('TENOR','DATE','MIXED')),

  /* 構築の由来（生DF/ゼロ入力か、ブートストラップで推定か） */
  build_method  TEXT NOT NULL DEFAULT 'RAW_DF'
                CHECK (build_method IN ('RAW_DF','RAW_ZERO','BOOTSTRAP')),

  /* 補助：どの暦でピラーを運用するか（必要時のみ） */
  cal_id        TEXT REFERENCES calendar_def(cal_id),

  /* 担保通貨（OIS割引の担保通貨と一致させたい場合など、将来拡張用） */
  collateral_ccy TEXT REFERENCES currency(ccy),

  /* 定義の有効期間（将来の改訂に備える）。valid_to=NULL は現行 */
  valid_from    TEXT NOT NULL DEFAULT '1970-01-01',
  valid_to      TEXT,

  description   TEXT,

  /* 同一キー空間での重複ガード。FORECAST 以外は ref_rate_id が NULL になる仕様 */
  UNIQUE (ccy, curve_type, IFNULL(ref_rate_id,''), valid_to)
);

CREATE INDEX IF NOT EXISTS ix_pricing_curve_def_ccy_type
  ON pricing_curve_def (ccy, curve_type);
CREATE INDEX IF NOT EXISTS ix_pricing_curve_def_select
  ON pricing_curve_def (ccy, curve_type, IFNULL(ref_rate_id,''), is_default, priority);

CREATE TABLE curve_point (
  snapshot_id   TEXT NOT NULL REFERENCES market_snapshot(snapshot_id),
  curve_id      TEXT NOT NULL REFERENCES pricing_curve_def(curve_id),

  /* ピラーの指定。TENOR モードなら tenor を、DATE モードなら pillar_date を使用 */
  tenor         TEXT,                         -- 例: '1D','1W','1M','6M','1Y','10Y'
  pillar_date   TEXT,                         -- 例: '2026-03-31'（DATE モード）

  /* 座標：評価起点 as_of からの年率時間（pricing_curve_def.daycount に基づく） */
  x_years       REAL NOT NULL,                -- 例: 0.5 (= 約半年)。補間・外挿の独立変数

  /* 値：DF と連続複利ゼロ（どちらか必須。両方があれば整合チェックが容易） */
  df            REAL,                         -- ディスカウントファクター
  zero_cont     REAL,                         -- 連続複利ゼロレート r（年率）
  CHECK (df IS NOT NULL OR zero_cont IS NOT NULL),

  /* 監査用メタ */
  is_extrapolated INTEGER NOT NULL DEFAULT 0, -- 1=外挿点（ピラー外の点）
  pillar_source   TEXT NOT NULL DEFAULT 'RAW'
                    CHECK (pillar_source IN ('RAW','BOOTSTRAP','DERIVED','SMOOTHED')),
  source_symbol   TEXT,                       -- ベンダの銘柄/コード等（任意）
  created_at      TEXT NOT NULL,              -- 取込UTC

  /* 主キー：同一スナップショット＋カーブで、ピラー一意（TENOR/DATE 併用を許容） */
  PRIMARY KEY (snapshot_id, curve_id, COALESCE(pillar_date, tenor))
);

CREATE INDEX IF NOT EXISTS ix_curve_point_curve_x
  ON curve_point (curve_id, x_years);

CREATE INDEX IF NOT EXISTS ix_curve_point_snapshot_curve
  ON curve_point (snapshot_id, curve_id);


/* FX インプライド・ボラティリティ（Garman–Kohlhagen 等で使用） */
CREATE TABLE vol_fx (
  /* 識別 */
  vol_id         TEXT PRIMARY KEY,                               -- 例: UUID（複合キーの煩雑さを避ける）
  snapshot_id    TEXT NOT NULL REFERENCES market_snapshot(snapshot_id),

  /* 通貨ペア（fx_spot と同じ正規化） */
  base_ccy       TEXT NOT NULL REFERENCES currency(ccy),         -- 分子通貨（例: USD）
  quote_ccy      TEXT NOT NULL REFERENCES currency(ccy),         -- 分母通貨（例: JPY）
  pair           TEXT NOT NULL,                                  -- 'USDJPY'
  CHECK (pair = base_ccy || quote_ccy),

  /* 満期（テナー or 実日付）＋ボルタイム（年率時間） */
  expiry_tenor   TEXT,                                           -- 例: '1W','1M','1Y'
  expiry_date    TEXT,                                           -- 例: '2026-03-31'
  x_years        REAL NOT NULL,                                  -- 年率時間（vol_time）
  vol_daycount   TEXT NOT NULL DEFAULT 'ACT/365F' 
                 REFERENCES daycount(code),                      -- ボラ年率化の規約（一般に ACT/365F）

  /* スマイル点の指定方法 */
  smile_type     TEXT NOT NULL CHECK (smile_type IN ('ATM','DELTA','STRIKE')),

  /* ATM の細分類（必要時のみ） */
  atm_type       TEXT CHECK (atm_type IN ('ATM_DN','ATM_FWD')),  -- DN=Delta-Neutral, FWD=Forward
  /* DELTA 指定（必要時のみ） */
  delta_type     TEXT CHECK (delta_type IN ('SPOT','FWD')),      -- デルタの定義
  premium_adj    INTEGER CHECK (premium_adj IN (0,1)),           -- 1=PA（プレミアム調整あり）, 0=NPA
  option_type    TEXT CHECK (option_type IN ('C','P')),          -- 'C' or 'P'
  delta_abs      REAL,                                           -- 0〜1（例: 0.25）
  /* STRIKE 指定（必要時のみ） */
  strike         REAL,                                           -- 行使価格（pair の価格単位）

  /* ボラクォート */
  quote_type     TEXT NOT NULL CHECK(quote_type IN ('LN_VOL','N_VOL')),  -- Black or Bachelier
  sigma          REAL NOT NULL,

  /* 監査メタ */
  quote_time_utc TEXT,                                           -- 観測UTC（ペア固有）
  source_symbol  TEXT,                                           -- ベンダ銘柄/ティッカー
  surface_tag    TEXT,                                           -- 任意の面タグ（'TRADABLE','CALIB','VENDOR_A' 等）
  created_at     TEXT NOT NULL,                                  -- 取込UTC

  /* 整合チェック：smile_type に応じて必須列を切替 */
  CHECK (
    (smile_type='ATM'    AND atm_type IS NOT NULL
                         AND delta_type IS NULL AND premium_adj IS NULL
                         AND option_type IS NULL AND delta_abs IS NULL AND strike IS NULL)
    OR
    (smile_type='DELTA'  AND delta_type IS NOT NULL
                         AND premium_adj IS NOT NULL
                         AND option_type IS NOT NULL
                         AND delta_abs IS NOT NULL
                         AND strike IS NULL)
    OR
    (smile_type='STRIKE' AND strike IS NOT NULL
                         AND delta_type IS NULL AND premium_adj IS NULL
                         AND option_type IS NULL AND delta_abs IS NULL)
  )
);

/* 一意性（業務重複防止）。数値列は NULL を番兵値に正規化して比較 */
CREATE UNIQUE INDEX IF NOT EXISTS ux_vol_fx_uq
ON vol_fx(
  snapshot_id, pair,
  COALESCE(expiry_date, ''), COALESCE(expiry_tenor, ''),
  smile_type, COALESCE(atm_type,''),
  COALESCE(delta_type,''), COALESCE(option_type,''),
  COALESCE(premium_adj,-1),
  COALESCE(delta_abs,-1.0),
  COALESCE(strike,-1.0),
  quote_type
);

CREATE INDEX IF NOT EXISTS ix_vol_fx_pair_expiry
  ON vol_fx (pair, COALESCE(expiry_date, ''), COALESCE(expiry_tenor, ''));


/* =========================
   Cap/Floor ボラティリティ
   ========================= */
CREATE TABLE vol_capfloor (
  vol_id         TEXT PRIMARY KEY,                                -- UUID 等
  snapshot_id    TEXT NOT NULL REFERENCES market_snapshot(snapshot_id),

  /* 通貨と参照インデックス（例：JPY-TONAR、USD-SOFR） */
  ccy            TEXT NOT NULL REFERENCES currency(ccy),
  ref_rate_id    TEXT,                                            -- 参照金利規約（index）。NULL可（旧設計互換）
  index_tenor    TEXT NOT NULL,                                   -- インデックスの期間（'1M','3M','6M' 等）

  /* 満期（caplet maturity）＋ボラ時間 */
  expiry_tenor   TEXT,                                            -- '1Y','5Y' 等
  expiry_date    TEXT,                                            -- 実日付指定が可能
  x_years        REAL NOT NULL,                                   -- 年率時間（vol_time）
  vol_daycount   TEXT NOT NULL DEFAULT 'ACT/365F' REFERENCES daycount(code),

  /* スマイル軸：ATM or STRIKE（レート） */
  smile_type     TEXT NOT NULL CHECK (smile_type IN ('ATM','STRIKE')),
  strike_rate    REAL,                                            -- 'STRIKE' のとき必須（実数、例: 0.01=1%）

  /* クォート種別と値 */
  quote_type     TEXT NOT NULL CHECK (quote_type IN ('LN_VOL','N_VOL')),
  sigma          REAL NOT NULL,

  /* 監査メタ */
  quote_time_utc TEXT,                                            -- 観測UTC
  source_symbol  TEXT,                                            -- ベンダ識別
  surface_tag    TEXT,                                            -- 面の用途タグ
  created_at     TEXT NOT NULL,                                   -- 取込UTC

  CHECK (
    (smile_type='ATM'    AND strike_rate IS NULL)
    OR
    (smile_type='STRIKE' AND strike_rate IS NOT NULL)
  ),

  /* 旧スキーマ互換：ccy×expiry×index_tenor×quote_type×smile で重複防止 */
  UNIQUE (snapshot_id, ccy,
          COALESCE(expiry_date,''), COALESCE(expiry_tenor,''),
          index_tenor, smile_type,
          COALESCE(strike_rate,-1.0), quote_type)
);

CREATE INDEX IF NOT EXISTS ix_vol_capfloor_key
  ON vol_capfloor (ccy, index_tenor,
                   COALESCE(expiry_date,''), COALESCE(expiry_tenor,''));


/* =========================
   Swaption ボラティリティ
   ========================= */
CREATE TABLE vol_swaption (
  vol_id         TEXT PRIMARY KEY,
  snapshot_id    TEXT NOT NULL REFERENCES market_snapshot(snapshot_id),

  ccy            TEXT NOT NULL REFERENCES currency(ccy),

  /* 満期（オプション満期）＋スワップ年限（基底スワップのテナー） */
  expiry_tenor   TEXT,
  expiry_date    TEXT,
  swap_tenor     TEXT NOT NULL,                                   -- '1Y','5Y','10Y' 等（基底スワップ年限）
  x_years        REAL NOT NULL,                                   -- オプションの年率時間
  vol_daycount   TEXT NOT NULL DEFAULT 'ACT/365F' REFERENCES daycount(code),

  /* 方式：ATM 中心（標準）。必要なら将来 STRIKE 軸を追加する想定 */
  quote_type     TEXT NOT NULL CHECK (quote_type IN ('LN_VOL','N_VOL')),
  sigma          REAL NOT NULL,

  /* 監査メタ */
  quote_time_utc TEXT,
  source_symbol  TEXT,
  surface_tag    TEXT,
  created_at     TEXT NOT NULL,

  UNIQUE (snapshot_id, ccy,
          COALESCE(expiry_date,''), COALESCE(expiry_tenor,''),
          swap_tenor, quote_type)
);

CREATE INDEX IF NOT EXISTS ix_vol_swaption_key
  ON vol_swaption (ccy, COALESCE(expiry_date,''), COALESCE(expiry_tenor,''), swap_tenor);


/* =========================
   モデル・パラメータ
   ========================= */
CREATE TABLE model_param (
  snapshot_id TEXT NOT NULL REFERENCES market_snapshot(snapshot_id),
  model_tag   TEXT NOT NULL,                                      -- 'BLACK','BACHELIER','BLACK_SHIFT','GK' 等
  scope       TEXT NOT NULL,                                      -- 'CCY','PAIR','INDEX','GLOBAL'
  param_key   TEXT NOT NULL,                                      -- 'JPY','USDJPY','JPY-TONAR' 等
  param_name  TEXT NOT NULL,                                      -- 'shift','beta','rho' 等
  param_val   REAL NOT NULL,
  param_unit  TEXT,                                               -- 'abs','bp','ratio' 等（任意）
  source_symbol TEXT,                                             -- ベンダ/由来
  note        TEXT,
  created_at  TEXT NOT NULL,
  PRIMARY KEY (snapshot_id, model_tag, scope, param_key, param_name)
);


/* =========================
   帳簿
   ========================= */
CREATE TABLE book (
  book_id     TEXT PRIMARY KEY,
  description TEXT,
  owner       TEXT,                                               -- 任意：責任者/Desk 名
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);


/* =========================
   取引
   ========================= */
CREATE TABLE trade (
  trade_id       TEXT PRIMARY KEY,
  product        TEXT NOT NULL REFERENCES m_trade_product(product),
  book_id        TEXT REFERENCES book(book_id),
  ccy            TEXT NOT NULL REFERENCES currency(ccy),          -- 報告/換算通貨
  notional       REAL NOT NULL,
  direction      INTEGER NOT NULL CHECK(direction IN (-1,1)),
  json_body      TEXT NOT NULL,                                   -- 商品固有項目（JSON1）
  trade_date     TEXT NOT NULL,
  effective_date TEXT,
  maturity_date  TEXT,
  is_active      INTEGER NOT NULL DEFAULT 1,
  valid_from     TEXT NOT NULL,
  created_at     TEXT NOT NULL,

  -- 監査補助
  external_id    TEXT,                                            -- 外部ID（任意）
  counterparty   TEXT                                             -- 相手先（任意）
);

CREATE INDEX IF NOT EXISTS idx_trade_product      ON trade(product);
CREATE INDEX IF NOT EXISTS idx_trade_book         ON trade(book_id);
CREATE INDEX IF NOT EXISTS idx_trade_json_pair    ON trade((json_extract(json_body,'$.pair')));
CREATE INDEX IF NOT EXISTS idx_trade_json_indexid ON trade((json_extract(json_body,'$.float_leg.index_id')));


/* =========================
   評価実行
   ========================= */
CREATE TABLE run (
  run_id          TEXT PRIMARY KEY,
  as_of           TEXT NOT NULL,
  snapshot_id     TEXT NOT NULL REFERENCES market_snapshot(snapshot_id),
  scenario_set_id TEXT,
  code_hash       TEXT NOT NULL,
  runner          TEXT,                                           -- 実行者/実行ホストなど
  created_at      TEXT NOT NULL
);


/* =========================
   評価結果
   ========================= */
CREATE TABLE result (
  run_id   TEXT NOT NULL REFERENCES run(run_id),
  trade_id TEXT NOT NULL REFERENCES trade(trade_id),
  measure  TEXT NOT NULL,                                         -- 'PV' など
  bucket   TEXT,                                                  -- 任意粒度キー
  val      REAL NOT NULL,
  ccy      TEXT NOT NULL REFERENCES currency(ccy),
  calc_time_ms REAL,                                              -- 任意：実行時間（計測/最適化評価用）
  PRIMARY KEY (run_id, trade_id, measure, COALESCE(bucket,''))
);

CREATE INDEX IF NOT EXISTS ix_result_measure ON result (measure);


/* =========================
   シナリオ
   ========================= */
CREATE TABLE scenario_set (
  scenario_set_id TEXT PRIMARY KEY,
  set_name        TEXT NOT NULL,
  description     TEXT,
  created_at      TEXT NOT NULL
);

CREATE TABLE scenario_shock (
  scenario_set_id TEXT NOT NULL REFERENCES scenario_set(scenario_set_id),
  scenario_id     INTEGER NOT NULL,                               -- 0,1,2...
  target          TEXT NOT NULL CHECK(target IN ('CURVE','FX','VOL','MODEL_PARAM')),
  target_key      TEXT NOT NULL,                                  -- 'JPY-OIS','USDJPY','vol_swaption:JPY' 等
  op_tag          TEXT NOT NULL CHECK(op_tag IN ('ADD_BP','MULT','SET')),
  shock_val       REAL NOT NULL,
  note            TEXT,                                           -- 任意メモ
  PRIMARY KEY (scenario_set_id, scenario_id, target, target_key, op_tag)
);

CREATE UNIQUE INDEX ux_currency_iso_numeric ON currency(iso_numeric);
