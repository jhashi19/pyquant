PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

/********** 0) 列挙マスタ **********/
CREATE TABLE IF NOT EXISTS m_calendar_role (
  role         TEXT PRIMARY KEY,            -- 例: 'DEFAULT','SETTLEMENT','HOLIDAY_ONLY','FIXING',...
  description  TEXT,                        -- 用途の説明
  created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- INSERT OR IGNORE INTO m_calendar_role (role, display_name, description, precedence)
-- VALUES
--   ('DEFAULT',       'Default',        '通貨の一般業務用のデフォルトカレンダー（非FXのスケジュール生成等）',            100),
--   ('SETTLEMENT',    'Settlement',     '決済用途（FXのSpot/T+N、受渡日、行使・決済日の共通営業日ANDなど）',         100),
--   ('HOLIDAY_ONLY',  'Holiday Only',   '休業日集合のみ参照（営業日ロールには使用しないUI/検証用）',                 200),
--   ('FIXING',        'Rate Fixing',    'Fixing/観測日用途のデフォルトカレンダー（必要になったら利用）',                   150),
--   ('EXCHANGE',      'Exchange',       '上場商品の取引所カレンダー（限月・最終売買日など）',                         150),
--   ('CLEARING',      'Clearing',       '清算機関（CCP）業務日カレンダー（証拠金や清算関連）',                       150),
--   ('DELIVERY',      'Physical Delivery','現物受渡拠点に紐づくカレンダー（コモディティ等で使用）',                  150);


CREATE TABLE m_interp_method (
  interp_method TEXT PRIMARY KEY,  -- 'LOG_LINEAR_DF','LINEAR_ZERO','PIECEWISE_CONST_FWD','CUBIC_SPLINE_ZERO'
  description  TEXT,              -- 用途の説明
  created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE m_extrap_method (
  extrap_method TEXT PRIMARY KEY,  -- 'FLAT_FWD','FLAT_ZERO','LINEAR_ZERO'など
  description  TEXT,              -- 用途の説明
  created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE m_trade_product (
  product      TEXT PRIMARY KEY,  -- 'IRS','BOND','IRFUT','FXFWD','FXOPT'など
  description  TEXT,              -- 用途の説明
  created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

/********** 1) 参照データ **********/
CREATE TABLE currency (
  ccy TEXT PRIMARY KEY CHECK(length(ccy)=3),
  name TEXT NOT NULL,                   -- 表示名: 'Japanese Yen', 'US Dollar'
  iso_numeric INTEGER UNIQUE,           -- ISO4217 数値コード: JPY=392, USD=840
  minor_unit INTEGER NOT NULL,          -- 小数桁: JPY=0, USD=2
  symbol TEXT,                          -- '¥', '$'
  spot_lag INTEGER NOT NULL DEFAULT 2,  -- FXスポット決済ラグ（営業日）
  enabled INTEGER NOT NULL DEFAULT 1,   -- 1=有効, 0=無効
  valid_from TEXT NOT NULL,             -- 'YYYY-MM-DD'
  retired_at TEXT,                      -- 廃止日
  created_at TEXT NOT NULL              -- 作成UTC
);

CREATE TABLE IF NOT EXISTS currency_calendar (
  ccy        TEXT NOT NULL REFERENCES currency(ccy),
  role       TEXT NOT NULL REFERENCES m_calendar_role(role) ON UPDATE CASCADE ON DELETE RESTRICT,
  cal_id     TEXT NOT NULL REFERENCES calendar_def(cal_id),
  enabled INTEGER NOT NULL DEFAULT 1,   -- 1=有効, 0=無効
  created_at TEXT NOT NULL,
  PRIMARY KEY (ccy, role),                  -- 1通貨×1役割 = 1行を基本とする
  -- UNIQUE (ccy, role, cal_id)                -- 同一通貨・役割で同一カレンダーを重複登録しない ←PKを考慮すると不要な条件
);

CREATE TABLE daycount (
  code TEXT PRIMARY KEY,                         -- 'ACT/360','ACT/365F','ACT/ACT-ISDA','30E/360' 等
  display_name TEXT NOT NULL,                    -- 表示名
  formula_tag TEXT NOT NULL UNIQUE,              -- 実装識別子: 'ACT_360','ACT_365F','ACT_ACT_ISDA','THIRTY_E_360' 等
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
  enabled INTEGER NOT NULL DEFAULT 1,   -- 1=有効, 0=無効
  valid_from TEXT NOT NULL,             -- 'YYYY-MM-DD'
  retired_at TEXT,                      -- 廃止日
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

/********** 2) マーケット・スナップショット **********/
CREATE TABLE market_snapshot (
  snapshot_id         TEXT PRIMARY KEY,                        -- 例: UUID
  as_of               TEXT NOT NULL,                           -- 論理市場日 'YYYY-MM-DD'
  as_of_tz            TEXT NOT NULL DEFAULT 'Asia/Tokyo',      -- 市場日の解釈TZ（IANA）。起算日→現地日を決める
  cut_label           TEXT,                                     -- 'EOD','NY_10AM','LDN_4PM' 等の運用カット名
  data_hash           TEXT NOT NULL,                            -- 入力一式の総ハッシュ（再現性同定）
  parent_snapshot_id  TEXT REFERENCES market_snapshot(snapshot_id), -- 親版（差分や派生元）
  is_locked           INTEGER NOT NULL DEFAULT 0,               -- 1=ロック済（以後不変の想定）
  qa_status           TEXT NOT NULL DEFAULT 'PENDING' CHECK(qa_status IN ('PENDING','APPROVED','REJECTED')), -- 品質審査状態
  note                TEXT,                                     -- 補足
  locked_at           TEXT,                                     -- 当版をロックしたUTC時刻（EOD確定の時刻）
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
  created_at       TEXT NOT NULL,
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
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),

  /* 同一キー空間での重複ガード。FORECAST 以外は ref_rate_id が NULL になる仕様 */
  UNIQUE (ccy, curve_type, IFNULL(ref_rate_id,''), valid_to)
);

CREATE INDEX IF NOT EXISTS ix_pricing_curve_def_ccy_type
  ON pricing_curve_def (ccy, curve_type);

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

CREATE TABLE IF NOT EXISTS ir_futures_def (
  fut_code           TEXT PRIMARY KEY,                         -- 金利先物銘柄コード（例: 'CME_SOFR3M','TSE_JGB10Y'）
  display_name       TEXT NOT NULL,                            -- 表示名（例: 'CME 3M SOFR Futures','JGB 10Y Futures'）
  exchange_code      TEXT NOT NULL,                            -- 取引所コード（例: 'CME','SGX','TSE'）
  ccy                TEXT NOT NULL REFERENCES currency(ccy),   -- 損益通貨（通常は原資産通貨）

  -- 先物が示唆する基準金利（任意）。可能であれば ref_rate_rule.index_id を設定する
  underlying_ref_rate_id TEXT REFERENCES ref_rate_rule(index_id),

  contract_notional  REAL NOT NULL,                            -- 1枚あたり想定元本（名目元本、例: 1_000_000）
  tick_size          REAL NOT NULL,                            -- 価格最小刻み幅（例: 0.0025）
  tick_value         REAL NOT NULL,                            -- 価格1tickあたりの金額価値（例: 12.5）

  -- 価格表現方法：'PRICE' = 100-価格型（例: 95.25 ⇒ 4.75%）, 'RATE' = レート直クォート
  quote_conv         TEXT NOT NULL CHECK (quote_conv IN ('PRICE','RATE')),

  -- 最終売買日算出のための規約（必要な銘柄のみ設定）
  last_trading_bdc   TEXT REFERENCES bizday_convention(code),  -- 最終売買日営業日規則（例: 'MODFOL'）
  last_trading_cal_id TEXT REFERENCES calendar_def(cal_id),    -- 最終売買日カレンダーID（取引所カレンダー等）

  notes              TEXT,                                     -- 契約仕様に関する補足メモ（例外条件など）
  enabled            INTEGER NOT NULL DEFAULT 1,               -- 有効フラグ（1=有効, 0=無効）
  created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS market_ir_futures (
  snapshot_id        TEXT NOT NULL REFERENCES market_snapshot(snapshot_id) ON DELETE CASCADE,
  fut_code           TEXT NOT NULL REFERENCES ir_futures_def(fut_code),
  contract_month     TEXT NOT NULL,                          -- 'YYYY-MM'
  price              REAL NOT NULL,                          -- 現在値（PRICE or RATE；ir_futures_def.quote_convで解釈）
  bid                REAL,
  ask                REAL,
  source_symbol      TEXT,                                   -- ベンダ銘柄ID
  created_at         TEXT NOT NULL,                          
  PRIMARY KEY (snapshot_id, fut_code, contract_month)
);

CREATE TABLE IF NOT EXISTS bond_def (
  security_id        TEXT PRIMARY KEY,                         -- 社内一意ID（文字列）
  isin               TEXT UNIQUE,                              -- 国際証券識別子（任意）
  local_code         TEXT,                                     -- JGB銘柄コード等（任意）
  name               TEXT NOT NULL,                            -- 表示名（発行体＋クーポン＋満期など）
  issuer             TEXT,                                     -- 発行体名（マスタ分割は将来でも可）
  ccy                TEXT NOT NULL REFERENCES currency(ccy),   -- 券面通貨
  coupon_type        TEXT NOT NULL CHECK (coupon_type IN ('FIX','FLOAT','ZC')),
  -- 固定債属性（coupon_type='FIX'）
  coupon_rate        REAL,                                     -- 年率（%表現ではなく実数、例 0.01）
  -- 変動債属性（coupon_type='FLOAT'）
  float_index_id     TEXT REFERENCES ref_rate_rule(index_id),
  float_spread       REAL,                                     -- 年率（実数）
  -- 共通の支払規約
  coupon_daycount    TEXT REFERENCES daycount(code),
  coupon_freq        TEXT,                                     -- '1Y','6M','3M' 等
  coupon_bdc         TEXT REFERENCES bizday_convention(code),
  coupon_cal_id      TEXT REFERENCES calendar_def(cal_id),

  redemption         REAL NOT NULL DEFAULT 100.0,              -- 額面償還（%基準、通常100）
  issue_date         TEXT,                                     -- 発行日
  maturity_date      TEXT NOT NULL,                            -- 満期日
  first_coupon_date  TEXT,                                     -- 初回クーポン（stub時）
  last_coupon_date   TEXT,                                     -- 最終クーポン（必要なら）

  settlement_days    INTEGER DEFAULT 2,                        -- 約定から決済までの営業日ラグ（T+2 等）
  prospectus_uri     TEXT,                                     -- 目論見書, term sheet 参照（任意）
  created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at         TEXT
  CHECK (
    (coupon_type='FIX'   AND coupon_rate IS NOT NULL)
    OR
    (coupon_type='FLOAT' AND float_index_id IS NOT NULL AND float_spread IS NOT NULL)
    OR
    (coupon_type='ZC'    AND coupon_rate IS NULL AND float_index_id IS NULL AND float_spread IS NULL)
  )
);

CREATE TABLE IF NOT EXISTS market_bond_price (
  snapshot_id   TEXT NOT NULL REFERENCES market_snapshot(snapshot_id) ON DELETE CASCADE,
  security_id   TEXT NOT NULL REFERENCES bond_def(security_id),
  clean_price   REAL,
  dirty_price   REAL,
  yield_to_mty  REAL,
  bid           REAL,
  ask           REAL,
  quote_ccy     TEXT REFERENCES currency(ccy),
  source_symbol TEXT,
  frozen_at     TEXT,
  created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  PRIMARY KEY (snapshot_id, security_id)
);
CREATE INDEX IF NOT EXISTS idx_mkt_bond_price_ccy ON market_bond_price(quote_ccy);


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


CREATE TABLE vol_swaption (
  vol_id         TEXT PRIMARY KEY,
  snapshot_id    TEXT NOT NULL REFERENCES market_snapshot(snapshot_id),

  ccy            TEXT NOT NULL REFERENCES currency(ccy),

  /* 満期（オプション満期）＋スワップ年限（基底スワップのテナー） */
  expiry_tenor   TEXT,
  expiry_date    TEXT,
  swap_tenor     TEXT NOT NULL,                                   -- '1Y','5Y','10Y' 等（基底スワップ年限）
  x_years        REAL NOT NULL,                                   -- オプションの年率時間
  vol_daycount   TEXT NOT NULL REFERENCES daycount(code),

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

/* 過去のFixing情報 */
CREATE TABLE historical_fixing (
  index_id       TEXT NOT NULL REFERENCES ref_rate_rule(index_id),
  fixing_date    TEXT NOT NULL,          -- 観測日
  rate           REAL NOT NULL,          -- 確定レート
  source_symbol  TEXT,
  created_at     TEXT NOT NULL,
  PRIMARY KEY (index_id, fixing_date)
);

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
   ポートフォリオ
   ========================= */
CREATE TABLE portfolio (
  portfolio_id        TEXT PRIMARY KEY,
  description         TEXT,
  owner               TEXT,                                               -- 任意：責任者/Desk 名
  parent_portfolio_id TEXT REFERENCES portfolio(portfolio_id),            -- 任意：階層（上位ポートフォリオ）
  portfolio_type      TEXT,                                               -- 任意：'DESK','STRATEGY','REPORTING','ADHOC' 等
  is_active           INTEGER NOT NULL DEFAULT 1,                          -- 1=有効,0=無効（論理停止）
  created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at          TEXT
);

CREATE INDEX IF NOT EXISTS idx_portfolio_parent
  ON portfolio(parent_portfolio_id);

CREATE INDEX IF NOT EXISTS idx_portfolio_active
  ON portfolio(is_active);



/* =========================
   取引
   ========================= */
CREATE TABLE trade (
  trade_id       TEXT PRIMARY KEY,
  product        TEXT NOT NULL REFERENCES m_trade_product(product),
  portfolio_id   TEXT REFERENCES portfolio(portfolio_id),
  ccy            TEXT NOT NULL REFERENCES currency(ccy),          -- 報告/換算通貨
  notional       REAL NOT NULL,
  direction       INTEGER NOT NULL CHECK (direction IN (-1,1)),  -- 単レグ商品の早見符号
  trade_date      TEXT NOT NULL,
  effective_date  TEXT,
  maturity_date   TEXT,
  status          TEXT CHECK (status IN (
                      'NEW','AMENDED','CANCELLED','EXERCISED','EXPIRED','NOVATED')),
  version_no      INTEGER,
  parent_trade_id TEXT REFERENCES trade(trade_id),
  replaced_by_trade_id TEXT REFERENCES trade(trade_id),
  is_active       INTEGER NOT NULL DEFAULT 1,
  external_id     TEXT,
  counterparty    TEXT,
  cleared_flag    INTEGER DEFAULT 0,
  clearing_house  TEXT,
  csa_id          TEXT,
  netting_set_id  TEXT,
  pricing_profile_id TEXT REFERENCES pricing_profile(profile_id),
  trader          TEXT,
  sales           TEXT,
  strategy_tag    TEXT,
  valid_from      TEXT NOT NULL,
  updated_at      TEXT,
  closed_at       TEXT,
  canceled_at     TEXT,
  source_tag      TEXT,
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_trade_product ON trade(product);
CREATE INDEX IF NOT EXISTS idx_trade_portfolio ON trade(portfolio_id);
CREATE INDEX IF NOT EXISTS idx_trade_portfolio_active ON trade(portfolio_id, is_active);

/* ============ 商品別：IRS ============ */
CREATE TABLE IF NOT EXISTS trade_irs (
  trade_id         TEXT PRIMARY KEY REFERENCES trade(trade_id) ON DELETE CASCADE,
  pay_rec          TEXT NOT NULL CHECK (pay_rec IN ('PAY','REC')),   -- 固定側の支払/受取
  fixed_rate       REAL NOT NULL,
  fixed_daycount   TEXT NOT NULL REFERENCES daycount(code),
  fixed_freq       TEXT NOT NULL,                                    -- '1Y','6M','3M' 等
  fixed_bdc        TEXT NOT NULL REFERENCES bizday_convention(code),
  fixed_cal_id     TEXT NOT NULL REFERENCES calendar_def(cal_id),

  float_index_id   TEXT NOT NULL REFERENCES ref_rate_rule(index_id),
  float_spread     REAL NOT NULL DEFAULT 0.0,
  float_daycount   TEXT NOT NULL REFERENCES daycount(code),
  float_freq       TEXT NOT NULL,
  float_bdc        TEXT NOT NULL REFERENCES bizday_convention(code),
  float_cal_id     TEXT NOT NULL REFERENCES calendar_def(cal_id),

  stub_type        TEXT,                                             -- 'FRONT','BACK','BOTH' など（必要ならCHECK拡張）
  settle_ccy       TEXT REFERENCES currency(ccy),
  created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS trade_irs_amortizing_schedule (
  trade_id       TEXT NOT NULL REFERENCES trade_irs(trade_id) ON DELETE CASCADE,
  step_no        INTEGER NOT NULL,       -- 1,2,3,... のステップ番号
  change_date    TEXT NOT NULL,          -- 'YYYY-MM-DD' 残高が変化する日（支払日基準など）
  notional_ratio REAL NOT NULL CHECK (notional_ratio > 0.0 AND notional_ratio <= 1.0),  -- 初期 notional に対する比率 (0.0〜1.0) 例: 0.8, 0.6, ...

  created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  PRIMARY KEY (trade_id, step_no),
  UNIQUE (trade_id, change_date)    -- 1 つの IRS で同じ change_date を重複登録しない
);

CREATE INDEX IF NOT EXISTS idx_irs_amortizing_trade_date
  ON trade_irs_amortizing_schedule(trade_id, change_date);


CREATE TABLE IF NOT EXISTS trade_bond (
  trade_id        TEXT PRIMARY KEY REFERENCES trade(trade_id) ON DELETE CASCADE,

  -- 銘柄参照（NULL可：私募や未整備銘柄にも対応）
  security_id     TEXT REFERENCES bond_def(security_id),

  -- 債券タイプ（取引側で上書き可能）
  coupon_type     TEXT NOT NULL CHECK (coupon_type IN ('FIX','FLOAT','ZC')),

  -- 固定債フィールド（coupon_type='FIX' のとき意味を持つ；非NULLなら銘柄を上書き）
  coupon_rate     REAL,
  coupon_daycount TEXT REFERENCES daycount(code),
  coupon_freq     TEXT,                                        -- '1Y','6M','3M' 等
  coupon_bdc      TEXT REFERENCES bizday_convention(code),
  coupon_cal_id   TEXT REFERENCES calendar_def(cal_id),

  -- 変動債フィールド（coupon_type='FLOAT' のとき意味を持つ）
  float_index_id  TEXT REFERENCES ref_rate_rule(index_id),
  float_spread    REAL,

  -- 共通
  issuer          TEXT,                                        -- 取引表示用の上書き（任意）
  redemption      REAL NOT NULL DEFAULT 100.0,                 -- 額面償還（%）
  settlement_ccy  TEXT NOT NULL REFERENCES currency(ccy),      -- 決済通貨（通常は券面通貨と同一）
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),

  -- 整合チェック（簡易）：タイプ別に主要必須の存在関係のみ担保
  CHECK (
    (coupon_type='FIX'   AND (coupon_rate IS NOT NULL OR security_id IS NOT NULL))
    OR
    (coupon_type='FLOAT' AND ((float_index_id IS NOT NULL AND float_spread IS NOT NULL) OR security_id IS NOT NULL))
    OR
    (coupon_type='ZC'    AND coupon_rate IS NULL AND float_index_id IS NULL AND float_spread IS NULL)
  )
);

/* ============ FX フォワード（据え置き） ============ */
CREATE TABLE IF NOT EXISTS trade_fxfwd (
  trade_id        TEXT PRIMARY KEY REFERENCES trade(trade_id) ON DELETE CASCADE,
  base_ccy        TEXT NOT NULL REFERENCES currency(ccy),
  quote_ccy       TEXT NOT NULL REFERENCES currency(ccy),
  pair            TEXT NOT NULL,
  deliver_date    TEXT NOT NULL,
  forward_rate    REAL NOT NULL,
  settle_bdc      TEXT NOT NULL REFERENCES bizday_convention(code),
  deliver_cal_id  TEXT NOT NULL REFERENCES calendar_def(cal_id),
  pay_rec_base    TEXT NOT NULL CHECK (pay_rec_base IN ('PAY','REC')),
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);


/* ============ 通貨オプション（バニラ＋バリアを統合） ============ */
CREATE TABLE IF NOT EXISTS trade_fxopt (
  trade_id        TEXT PRIMARY KEY REFERENCES trade(trade_id) ON DELETE CASCADE,
  base_ccy        TEXT NOT NULL REFERENCES currency(ccy),
  quote_ccy       TEXT NOT NULL REFERENCES currency(ccy),
  pair            TEXT NOT NULL,

  /* バニラ共通 */
  cp_flag         TEXT NOT NULL CHECK (cp_flag IN ('C','P')),                           -- Call/Put
  option_style    TEXT NOT NULL CHECK (option_style IN ('EUROPEAN','AMERICAN','BERMUDAN')),
  strike          REAL NOT NULL,
  expiry_date     TEXT NOT NULL,
  settlement      TEXT NOT NULL CHECK (settlement IN ('PHYS','CASH')),
  premium_ccy     TEXT REFERENCES currency(ccy),
  premium_amount  REAL,
  deliver_cal_id  TEXT REFERENCES calendar_def(cal_id),
  exercise_cal_id TEXT REFERENCES calendar_def(cal_id),

  /* バリア拡張（該当しない場合は NULL） */
  barrier_type    TEXT CHECK (barrier_type IN ('KI','KO')),
  barrier_dir     TEXT CHECK (barrier_dir  IN ('UP','DOWN')),
  barrier_level   REAL,
  rebate_type     TEXT CHECK (rebate_type IN ('AT_HIT','AT_EXPIRY')),
  rebate_ccy      TEXT REFERENCES currency(ccy),
  rebate_amount   REAL,
  monitoring_cal_id TEXT REFERENCES calendar_def(cal_id),
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),

  /* 整合チェック：バリア指定の有無で一貫性を担保（簡易） */
  CHECK (
    (barrier_type IS NULL AND barrier_dir IS NULL AND barrier_level IS NULL
                          AND rebate_type IS NULL AND rebate_ccy IS NULL AND rebate_amount IS NULL)
    OR
    (barrier_type IS NOT NULL AND barrier_dir IS NOT NULL AND barrier_level IS NOT NULL)
  )
);
CREATE INDEX IF NOT EXISTS idx_fxopt_pair_expiry ON trade_fxopt(pair, expiry_date);


/* ============ Cap/Floor（据え置き） ============ */
CREATE TABLE IF NOT EXISTS trade_capfloor (
  trade_id        TEXT PRIMARY KEY REFERENCES trade(trade_id) ON DELETE CASCADE,
  ccy             TEXT NOT NULL REFERENCES currency(ccy),
  cp_flag         TEXT NOT NULL CHECK (cp_flag IN ('C','P')),        -- 'C'=Cap, 'P'=Floor
  index_id        TEXT NOT NULL REFERENCES ref_rate_rule(index_id),
  index_tenor     TEXT NOT NULL,
  strike_rate     REAL NOT NULL,
  start_date      TEXT NOT NULL,
  end_date        TEXT NOT NULL,
  pay_rec         TEXT NOT NULL CHECK (pay_rec IN ('PAY','REC')),
  pay_freq        TEXT NOT NULL,
  pay_bdc         TEXT NOT NULL REFERENCES bizday_convention(code),
  pay_cal_id      TEXT NOT NULL REFERENCES calendar_def(cal_id),
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);


/* ============ Swaption（EU/AM/BER を 1 テーブルで表現） ============ */
CREATE TABLE IF NOT EXISTS trade_swaption (
  trade_id          TEXT PRIMARY KEY REFERENCES trade(trade_id) ON DELETE CASCADE,
  ccy               TEXT NOT NULL REFERENCES currency(ccy),

  /* 行使スタイル＋ payer/receiver 軸 */
  option_style      TEXT NOT NULL CHECK (option_style IN ('EUROPEAN','AMERICAN','BERMUDAN')),
  cp_flag           TEXT NOT NULL CHECK (cp_flag IN ('C','P')),      -- 'C'=payer, 'P'=receiver（慣用）

  /* 満期・行使情報 */
  expiry_date       TEXT NOT NULL,                                   -- EU では権利行使日
  exercise_open     TEXT,                                            -- AM：行使開始（通常は取引発効）
  exercise_close    TEXT,                                            -- AM：行使終了（=expiry_date など）

  settlement        TEXT NOT NULL CHECK (settlement IN ('PHYS','CASH')),

  /* 基底スワップ規約（固定レグ） */
  swap_pay_rec      TEXT NOT NULL CHECK (swap_pay_rec IN ('PAY','REC')),
  swap_fixed_rate   REAL,                                            -- ATMF なら NULL
  swap_fixed_dc     TEXT NOT NULL REFERENCES daycount(code),
  swap_fixed_freq   TEXT NOT NULL,
  swap_fixed_bdc    TEXT NOT NULL REFERENCES bizday_convention(code),
  swap_fixed_cal    TEXT NOT NULL REFERENCES calendar_def(cal_id),

  /* 基底スワップ規約（変動レグ） */
  swap_index_id     TEXT NOT NULL REFERENCES ref_rate_rule(index_id),
  swap_index_tenor  TEXT NOT NULL,
  swap_spread       REAL NOT NULL DEFAULT 0.0,
  swap_float_dc     TEXT NOT NULL REFERENCES daycount(code),
  swap_float_freq   TEXT NOT NULL,
  swap_float_bdc    TEXT NOT NULL REFERENCES bizday_convention(code),
  swap_float_cal    TEXT NOT NULL REFERENCES calendar_def(cal_id),

  swap_maturity     TEXT NOT NULL,
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),

  /* 整合チェック（簡易）：スタイル別の行使情報の有無 */
  CHECK (
    (option_style='EUROPEAN' AND exercise_open IS NULL AND exercise_close IS NULL)
    OR
    (option_style='AMERICAN' AND exercise_open IS NOT NULL AND exercise_close IS NOT NULL)
    OR
    (option_style='BERMUDAN' AND exercise_open IS NULL AND exercise_close IS NULL)
  )
);
CREATE INDEX IF NOT EXISTS idx_swaption_expiry ON trade_swaption(expiry_date);

CREATE TABLE IF NOT EXISTS trade_swaption_bermudan_exercise (
  trade_id      TEXT NOT NULL REFERENCES trade_swaption(trade_id) ON DELETE CASCADE,
  seq_no        INTEGER NOT NULL,        -- 1,2,3,... 行使順序
  exercise_date TEXT NOT NULL,           -- 'YYYY-MM-DD'

  created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  PRIMARY KEY (trade_id, seq_no),
  UNIQUE (trade_id, exercise_date)    -- 1 つの Swaption で同一日付を重複登録しない
);

CREATE INDEX IF NOT EXISTS idx_swaption_berm_date
  ON trade_swaption_bermudan_exercise(exercise_date);


CREATE TABLE IF NOT EXISTS trade_ir_futures (
  trade_id           TEXT PRIMARY KEY REFERENCES trade(trade_id) ON DELETE CASCADE,
  fut_code           TEXT NOT NULL REFERENCES ir_futures_def(fut_code),
  contract_month     TEXT NOT NULL,                          -- 'YYYY-MM'（限月）
  last_trading_date  TEXT,                                   -- 明示指定（NULLは規約から導出）
  position_lots      INTEGER NOT NULL,                       -- 枚数（ロング>0/ショート<0）
  price_agreed       REAL NOT NULL,                          -- 約定時の先物価格（PRICE or RATE；quote_conv参照）
  margin_style       TEXT NOT NULL CHECK (margin_style IN ('EXCHANGE','BILATERAL')),
  cal_id_override    TEXT REFERENCES calendar_def(cal_id),   -- 取引所カレンダー明示（通常NULL）
  created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS trade_fra (
  trade_id           TEXT PRIMARY KEY REFERENCES trade(trade_id) ON DELETE CASCADE,
  ccy                TEXT NOT NULL REFERENCES currency(ccy),
  notional           REAL NOT NULL,
  pay_rec            TEXT NOT NULL CHECK (pay_rec IN ('PAY','REC')), -- 固定支払/受取（FRAレートの方向）
  fra_rate_agreed    REAL NOT NULL,                                  -- 約定FRAレート（固定）
  ref_rate_id        TEXT NOT NULL REFERENCES ref_rate_rule(ref_rate_id), -- 観測指標（例: 'USD-SOFR-3M','JPY-TONAR-3M'）
  accrual_start_date TEXT NOT NULL,                                   -- 開始日
  accrual_end_date   TEXT NOT NULL,                                   -- 終了日
  daycount           TEXT NOT NULL REFERENCES daycount(code),
  pay_bdc            TEXT NOT NULL REFERENCES bizday_convention(code),
  pay_cal_id         TEXT NOT NULL REFERENCES calendar_def(cal_id),
  fixing_lag_bd      INTEGER NOT NULL DEFAULT 0,                      -- 観測ラグ（営業日）
  fixing_bdc         TEXT NOT NULL REFERENCES bizday_convention(code),
  fixing_cal_id      TEXT NOT NULL REFERENCES calendar_def(cal_id),
  settlement_type    TEXT NOT NULL CHECK (settlement_type IN ('CASH')), -- v1はキャッシュ決済のみ
  created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE trade_schedule (
  trade_id       TEXT NOT NULL REFERENCES trade(trade_id) ON DELETE CASCADE,
  leg_id         TEXT NOT NULL,       -- レッグ識別子：商品ごとに 'PAY' / 'REC' や 'LEG1' / 'LEG2' 等の文字列を使用
  payment_date   TEXT NOT NULL,          -- 支払日
  start_date     TEXT,                   -- 利息計算期間開始
  end_date       TEXT,                   -- 利息計算期間終了
  payment_type   TEXT NOT NULL,          -- 'INTEREST', 'PRINCIPAL', 'FEE' 等
  currency       TEXT NOT NULL REFERENCES currency(ccy),
  notional       REAL,                   -- 計算基準元本
  rate           REAL,                   -- 適用金利（変動の場合はFixing済み、未定ならNULLまたは予測値）
  forecast_amount REAL,                  -- 現在のマーケットでの予測支払額
  fixed_amount   REAL,                   -- 確定した支払額（Fixing後）
  is_settled     INTEGER NOT NULL DEFAULT 0,      -- 決済済みフラグ：0=未決済, 1=決済済み
  created_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  PRIMARY KEY (trade_id, leg_id, payment_date, payment_type)
);

/* 取引×レッグ単位での検索用 */
CREATE INDEX IF NOT EXISTS idx_trade_schedule_trade_leg
  ON trade_schedule(trade_id, leg_id);

/* 評価日別キャッシュフロー集計など、日付横断の検索用 */
CREATE INDEX IF NOT EXISTS idx_trade_schedule_payment_date
  ON trade_schedule(payment_date);

CREATE TABLE IF NOT EXISTS measure_def (
  measure_id           TEXT PRIMARY KEY,
  measure_name         TEXT NOT NULL,
  category             TEXT NOT NULL, -- 'VALUATION','PL','SENSITIVITY','REGULATORY'
  unit                 TEXT NOT NULL, -- 'CCY','BP','PCT','NONE'
  default_calc_method  TEXT NOT NULL, -- 'FULL_REVAL','BUMP_REVAL','ANALYTIC'
  preferred_store      TEXT NOT NULL, -- 'CORE','EXT','REG'
  enabled              INTEGER NOT NULL DEFAULT 1,
  description          TEXT,
  created_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at           TEXT
);

/* =========================
   評価実行
   ========================= */
CREATE TABLE IF NOT EXISTS run (
  run_id              TEXT PRIMARY KEY,

  /* どのプリセットから起動されたか（任意）。プリセット変更後でも run_* 側で再現できる */
  preset_id           TEXT REFERENCES eval_preset(preset_id) ON DELETE SET NULL,

  /* UI表示用のラベル（例: "EOD JPY Book", "Stress-USD" など）。プリセット名のスナップショット用途にも使える */
  run_name            TEXT,

  /* UIで「実行者」を明示したい（plan_UIの要件） */
  requested_by_user_id TEXT REFERENCES app_user(user_id) ON DELETE SET NULL,

  /* 任意メモ（起動理由、比較用タグ等） */
  request_note        TEXT,

  /* 再現性の核：評価日・市場版・シナリオ */
  as_of               TEXT NOT NULL,  -- 'YYYY-MM-DD'
  snapshot_id         TEXT NOT NULL REFERENCES market_snapshot(snapshot_id) ON DELETE RESTRICT,
  scenario_set_id     TEXT REFERENCES scenario_set(scenario_set_id) ON DELETE RESTRICT,

  /* 再現性の核：コード版/設定版 */
  code_hash           TEXT NOT NULL,
  config_hash         TEXT,           -- pricing_profile 等の「コード外設定」を同定するハッシュ（任意だが推奨）
  input_hash          TEXT,           -- (as_of,snapshot_id,scenario_set_id,code_hash,run_* 等) をまとめた同定値

  /* 実行主体（ホスト/ワーカー/バッチ名など）は引き続き自由文字列で保持 */
  runner              TEXT,

  /* 状態と時刻（QUEUED を許すなら started_at は NULL 可にする） */
  status              TEXT NOT NULL CHECK (status IN
                        ('QUEUED','RUNNING','SUCCESS','FAILED','WARNING','CANCELLED')),
  created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),  -- 受付/登録時刻
  started_at          TEXT,   -- RUNNING になった時点でセット
  finished_at         TEXT,   -- 終了時刻

  /* UIに出したい典型情報（障害調査/運転管理） */
  error_code          TEXT,
  error_message       TEXT,

  /* UIの一覧やサマリで即表示したいカウンタ（結果表から集計も可能だが、先に持つと軽い） */
  trade_count         INTEGER,   -- 対象取引数（確定後にセット）
  measure_count       INTEGER,   -- 対象メジャー数
  scenario_count      INTEGER,   -- 対象シナリオ数（ベース含む）
  total_calc_time_ms  REAL       -- 任意：集計計算時間
);

CREATE INDEX IF NOT EXISTS ix_run_asof_created
  ON run (as_of, created_at);

CREATE INDEX IF NOT EXISTS ix_run_status_created
  ON run (status, created_at);

CREATE INDEX IF NOT EXISTS ix_run_snapshot
  ON run (snapshot_id);

CREATE INDEX IF NOT EXISTS ix_run_preset
  ON run (preset_id);


/* run ごとの対象ポートフォリオ（解決済み） */
CREATE TABLE IF NOT EXISTS run_portfolio (
  run_id  TEXT NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
  portfolio_id TEXT NOT NULL REFERENCES portfolio(portfolio_id),
  PRIMARY KEY (run_id, portfolio_id)
);
CREATE INDEX IF NOT EXISTS idx_run_portfolio_portfolio
  ON run_portfolio(portfolio_id);

/* run ごとの対象通貨（解決済み） */
CREATE TABLE IF NOT EXISTS run_ccy (
  run_id TEXT NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
  ccy    TEXT NOT NULL REFERENCES currency(ccy),
  PRIMARY KEY (run_id, ccy)
);
CREATE INDEX IF NOT EXISTS idx_run_ccy_ccy
  ON run_ccy(ccy);

/* run ごとの対象メジャー（解決済み） */
CREATE TABLE IF NOT EXISTS run_measure (
  run_id     TEXT NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
  measure_id TEXT NOT NULL REFERENCES measure_def(measure_id),
  PRIMARY KEY (run_id, measure_id)
);
CREATE INDEX IF NOT EXISTS idx_run_measure_measure
  ON run_measure(measure_id);


/* run ごとの実行パラメータ（JSONを使わずKey/Valueで保持） */
CREATE TABLE IF NOT EXISTS run_param (
  run_id    TEXT NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
  param_key TEXT NOT NULL,     -- 例: 'rng_seed','num_paths','bump_bp','engine_tag' 等
  val_text  TEXT,
  val_int   INTEGER,
  val_real  REAL,
  unit      TEXT,              -- 例: 'bp','paths','ms' 等（任意）
  note      TEXT,
  PRIMARY KEY (run_id, param_key),
  CHECK (val_text IS NOT NULL OR val_int IS NOT NULL OR val_real IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_run_param_key
  ON run_param(param_key);



/* =========================
   評価結果
   ========================= */

/* 日次評価結果（PV/日次PL 等の基本結果） */
CREATE TABLE result_eod (
  run_id        TEXT NOT NULL REFERENCES run(run_id),
  trade_id      TEXT NOT NULL REFERENCES trade(trade_id),

  measure       TEXT NOT NULL REFERENCES measure_def(measure_id),  -- 'PV','PL','ACCRUAL' 等（主に VALUATION/PL）
  scenario_id   INTEGER NOT NULL DEFAULT 0,                        -- 0=ベースケース
  bucket        TEXT NOT NULL DEFAULT '',                         -- 追加粒度キー（未指定は空文字）

  val           REAL NOT NULL,
  ccy           TEXT NOT NULL REFERENCES currency(ccy),

  calc_time_ms  REAL,                                             -- 任意：性能計測用

  PRIMARY KEY (run_id, trade_id, measure, scenario_id, bucket)
);

CREATE INDEX IF NOT EXISTS ix_result_eod_measure
  ON result_eod (measure);

CREATE INDEX IF NOT EXISTS ix_result_eod_trade
  ON result_eod (trade_id);


/* 感応度結果（DV01/Delta/Vega 等） */
CREATE TABLE result_sensitivity (
  run_id        TEXT NOT NULL REFERENCES run(run_id),
  trade_id      TEXT NOT NULL REFERENCES trade(trade_id),

  measure       TEXT NOT NULL REFERENCES measure_def(measure_id),  -- category='SENSITIVITY' を主対象
  scenario_id   INTEGER NOT NULL DEFAULT 0,                        -- シナリオ型感応度に拡張する余地
  bucket        TEXT NOT NULL DEFAULT '',

  val           REAL NOT NULL,
  ccy           TEXT NOT NULL REFERENCES currency(ccy),

  calc_time_ms  REAL,

  PRIMARY KEY (run_id, trade_id, measure, scenario_id, bucket)
);

CREATE INDEX IF NOT EXISTS ix_result_sens_measure
  ON result_sensitivity (measure);

CREATE INDEX IF NOT EXISTS ix_result_sens_trade
  ON result_sensitivity (trade_id);


/* シミュレーション／ストレス結果 */
CREATE TABLE result_simulation (
  run_id        TEXT NOT NULL REFERENCES run(run_id),
  trade_id      TEXT NOT NULL REFERENCES trade(trade_id),

  measure       TEXT NOT NULL REFERENCES measure_def(measure_id),  -- 'PV','PL' 等
  scenario_id   INTEGER NOT NULL DEFAULT 0,                        -- scenario_set 内の番号（0=ベース）
  bucket        TEXT NOT NULL DEFAULT '',                         -- 将来の時系列・パス等の追加軸

  val           REAL NOT NULL,
  ccy           TEXT NOT NULL REFERENCES currency(ccy),

  calc_time_ms  REAL,

  PRIMARY KEY (run_id, trade_id, measure, scenario_id, bucket)
);

CREATE INDEX IF NOT EXISTS ix_result_sim_measure
  ON result_simulation (measure);

CREATE INDEX IF NOT EXISTS ix_result_sim_trade
  ON result_simulation (trade_id);

/* =========================
   プライシングプロファイル
   ========================= */
CREATE TABLE IF NOT EXISTS pricing_profile (
  profile_id    TEXT PRIMARY KEY, -- 内部ID。例: 'STD_OIS','CSA_A_OIS'。trade.pricing_profile_id から参照される
  profile_name  TEXT NOT NULL UNIQUE, -- UI表示名。例: '標準OIS割引','CSA A 用OIS割引'
  description   TEXT,              -- プロファイルの詳細説明（使用するカーブの方針など）
  enabled       INTEGER NOT NULL DEFAULT 1, -- 1=有効, 0=無効（論理削除的な意味合い）
  created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at    TEXT
);

/* プロファイル別マーケットデータマッピング
   - profile_id + product + ccy + md_role で一意
   - md_role は 'DISCOUNT_CURVE','FORECAST_CURVE','FX_OPTION_VOL','CAPFLOOR_VOL','SWAPTION_VOL' 等を想定
   - md_id は pricing_curve_def.curve_id や vol_* の vol_id など、実際に参照するマーケットデータID
*/
CREATE TABLE IF NOT EXISTS pricing_profile_map (
  profile_id   TEXT NOT NULL REFERENCES pricing_profile(profile_id),
  product      TEXT NOT NULL REFERENCES m_trade_product(product), -- 'IRS','BOND','FXFWD','FXOPT' など
  ccy          TEXT NOT NULL REFERENCES currency(ccy),

  md_role      TEXT NOT NULL, -- 'DISCOUNT_CURVE','FORECAST_CURVE','FX_OPTION_VOL','CAPFLOOR_VOL','SWAPTION_VOL' など
  md_id        TEXT NOT NULL, -- 参照するマーケットデータID（curve_id や vol_id 等）

  priority     INTEGER NOT NULL DEFAULT 100, -- マッチング優先度（小さいほど高優先度）
  note         TEXT,

  PRIMARY KEY (profile_id, product, ccy, md_role)
);

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
  created_at      TEXT NOT NULL,
  PRIMARY KEY (scenario_set_id, scenario_id, target, target_key, op_tag)
);

CREATE UNIQUE INDEX ux_currency_iso_numeric ON currency(iso_numeric);

/* =========================
   アプリケーションユーザ・権限管理
   ========================= */

CREATE TABLE IF NOT EXISTS app_user (
  user_id             TEXT PRIMARY KEY,                                        -- ログインID兼アプリ内ユーザID
  display_name        TEXT NOT NULL,                                           -- 表示名
  password_hash       TEXT NOT NULL,                                           -- 認証用ハッシュ
  salt                TEXT NOT NULL,                                           -- ハッシュ用ソルト
  password_updated_at TEXT,                                                    -- パスワード最終更新日時（UTC）
  enabled             INTEGER NOT NULL DEFAULT 1,                              -- 1=有効,0=無効
  created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at          TEXT,
  note                TEXT                                                     -- 管理用メモ
);

CREATE TABLE IF NOT EXISTS role (
  role_id     TEXT PRIMARY KEY,                                                -- 'ADMIN','TRADER','VIEWER' 等
  role_name   TEXT NOT NULL,                                                   -- 表示名
  description TEXT,                                                            -- ロールの説明
  is_system   INTEGER NOT NULL DEFAULT 0,                                      -- 1=システム予約ロール
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS user_role (
  user_id    TEXT NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
  role_id    TEXT NOT NULL REFERENCES role(role_id)       ON DELETE CASCADE,
  granted_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  granted_by TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY (user_id, role_id)
);

CREATE TABLE IF NOT EXISTS eval_preset (
  preset_id        TEXT PRIMARY KEY,                                          -- プリセットID（UUID/コード）
  preset_name      TEXT NOT NULL,                                             -- プリセット名
  description      TEXT,                                                      -- 説明
  owner_user_id    TEXT REFERENCES app_user(user_id) ON DELETE SET NULL,      -- 作成ユーザ（NULL=グローバル）
  as_of_policy     TEXT NOT NULL,                                             -- 'FIXED','TODAY','RELATIVE','PREV_BUSINESS_DAY' 等
  as_of_fixed      TEXT,                                                      -- 固定評価日（policy='FIXED' 用）
  snapshot_policy  TEXT NOT NULL,                                             -- 'LATEST_LOCKED','BY_ID','BY_TAG' 等
  snapshot_id_fixed TEXT,                                                     -- 固定スナップショットID（BY_ID 用）
  scenario_set_id  TEXT REFERENCES scenario_set(scenario_set_id) ON DELETE SET NULL,
  portfolio_scope       TEXT NOT NULL CHECK (portfolio_scope IN ('ALL','PORTFOLIO_LIST','PORTFOLIO_PREFIX')),
  ccy_scope        TEXT NOT NULL CHECK (ccy_scope  IN ('ALL','CCY_LIST','CCY_BASE')),
  is_active        INTEGER NOT NULL DEFAULT 1,                                -- 1=有効,0=無効
  created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  created_by       TEXT,                                                      -- 作成者ID
  updated_at       TEXT,
  updated_by       TEXT
);

CREATE INDEX IF NOT EXISTS ix_eval_preset_active_owner
  ON eval_preset (is_active, owner_user_id);

-- 対象ポートフォリオ（portfolio_scope='PORTFOLIO_LIST' のとき有効）
CREATE TABLE IF NOT EXISTS eval_preset_portfolio (
  preset_id TEXT NOT NULL REFERENCES eval_preset(preset_id) ON DELETE CASCADE,
  portfolio_id TEXT NOT NULL REFERENCES portfolio(portfolio_id),
  PRIMARY KEY (preset_id, portfolio_id)
);

CREATE INDEX IF NOT EXISTS idx_eval_preset_portfolio_portfolio
  ON eval_preset_portfolio(portfolio_id);


-- 対象通貨（ccy_scope='CCY_LIST' のとき有効）
CREATE TABLE IF NOT EXISTS eval_preset_ccy (
  preset_id TEXT NOT NULL REFERENCES eval_preset(preset_id) ON DELETE CASCADE,
  ccy       TEXT NOT NULL REFERENCES currency(ccy),
  PRIMARY KEY (preset_id, ccy)
);

CREATE INDEX IF NOT EXISTS idx_eval_preset_ccy_ccy
  ON eval_preset_ccy(ccy);


-- 計算メジャー（常に 1 件以上存在する想定）
CREATE TABLE IF NOT EXISTS eval_preset_measure (
  preset_id    TEXT NOT NULL REFERENCES eval_preset(preset_id) ON DELETE CASCADE,
  measure_code TEXT NOT NULL,      -- 'PV','PL','DELTA','GAMMA','VEGA',... を想定
  PRIMARY KEY (preset_id, measure_code)
);
