PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

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
  quote_time_utc   TEXT,                                     -- このペア固有の観測UTC時刻（任意）
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
  curve_id TEXT PRIMARY KEY,              -- 'JPY-OIS','USD-OIS','USD-SOFR-3M'
  ccy TEXT NOT NULL REFERENCES currency(ccy),
  curve_type TEXT NOT NULL,               -- 'OIS','FORECAST','BOND' 等
  index_id TEXT,                          -- 予測曲線なら紐付け
  daycount TEXT NOT NULL REFERENCES daycount(code),
  compounding TEXT NOT NULL DEFAULT 'CONT', -- 'CONT','SIMPLE','ANNUAL'
  cal_id TEXT,                            -- 必要に応じて
  UNIQUE (ccy, curve_type, ifnull(index_id,''))
);

CREATE TABLE curve_point (
  snapshot_id TEXT NOT NULL REFERENCES market_snapshot(snapshot_id),
  curve_id TEXT NOT NULL REFERENCES pricing_curve_def(curve_id),
  tenor TEXT NOT NULL,                    -- '1D','1W','1M','6M','1Y','5Y' 等
  pillar_date TEXT,                       -- 実日付を使う場合
  zero_rate REAL,                         -- 連続複利年率（片方のみ使用）
  discount_factor REAL,                   -- 割引因子
  PRIMARY KEY (snapshot_id, curve_id, COALESCE(pillar_date, tenor))
);

CREATE TABLE vol_fx (
  snapshot_id TEXT NOT NULL REFERENCES market_snapshot(snapshot_id),
  pair TEXT NOT NULL,                     -- 'USDJPY'
  expiry TEXT NOT NULL,                   -- '1W','1M','1Y' 等
  strike_tag TEXT NOT NULL,               -- ATM, K=150 等
  quote_type TEXT NOT NULL CHECK(quote_type IN ('LN_VOL','N_VOL')),
  sigma REAL NOT NULL,
  PRIMARY KEY (snapshot_id, pair, expiry, strike_tag, quote_type)
);

CREATE TABLE vol_capfloor (
  snapshot_id TEXT NOT NULL REFERENCES market_snapshot(snapshot_id),
  ccy TEXT NOT NULL REFERENCES currency(ccy),
  expiry TEXT NOT NULL,                   -- caplet maturity
  tenor TEXT NOT NULL,                    -- underlying accrual tenor e.g. '3M'
  quote_type TEXT NOT NULL CHECK(quote_type IN ('LN_VOL','N_VOL')),
  sigma REAL NOT NULL,
  PRIMARY KEY (snapshot_id, ccy, expiry, tenor, quote_type)
);

CREATE TABLE vol_swaption (
  snapshot_id TEXT NOT NULL REFERENCES market_snapshot(snapshot_id),
  ccy TEXT NOT NULL REFERENCES currency(ccy),
  expiry TEXT NOT NULL,                   -- option expiry '1Y','2Y'...
  tenor TEXT NOT NULL,                    -- swap tenor '5Y','10Y'...
  quote_type TEXT NOT NULL CHECK(quote_type IN ('LN_VOL','N_VOL')),
  sigma REAL NOT NULL,
  PRIMARY KEY (snapshot_id, ccy, expiry, tenor, quote_type)
);

CREATE TABLE model_param (
  snapshot_id TEXT NOT NULL REFERENCES market_snapshot(snapshot_id),
  model_tag TEXT NOT NULL,                -- 'BLACK','BACHELIER','BLACK_SHIFT','GK'
  scope TEXT NOT NULL,                    -- 'CCY','PAIR','INDEX','GLOBAL'
  param_key TEXT NOT NULL,                -- 'JPY','USDJPY','USD-SOFR' 等
  param_name TEXT NOT NULL,               -- 'shift','beta','rho' 等
  param_val REAL NOT NULL,
  PRIMARY KEY (snapshot_id, model_tag, scope, param_key, param_name)
);

/********** 3) 取引 **********/
CREATE TABLE book (
  book_id TEXT PRIMARY KEY,
  description TEXT
);

CREATE TABLE trade (
  trade_id TEXT PRIMARY KEY,
  product TEXT NOT NULL CHECK(product IN (
    'IRS','BOND_FIXED','BOND_FLOAT','BOND_ZC','FXFWD','FXOPT_EU','CAPFLOOR','SWAPTION_EU'
  )),
  book_id TEXT REFERENCES book(book_id),
  ccy TEXT NOT NULL REFERENCES currency(ccy),     -- 報告通貨 or 主要通貨
  notional REAL NOT NULL,
  direction INTEGER NOT NULL CHECK(direction IN (-1,1)), -- 運用定義
  json_body TEXT NOT NULL,                        -- 商品固有項目（JSON1）
  trade_date TEXT NOT NULL,
  effective_date TEXT,
  maturity_date TEXT,
  is_active INTEGER NOT NULL DEFAULT 1,
  valid_from TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE INDEX idx_trade_product ON trade(product);
CREATE INDEX idx_trade_book ON trade(book_id);
CREATE INDEX idx_trade_json_pair ON trade((json_extract(json_body,'$.pair')));
CREATE INDEX idx_trade_json_indexid ON trade((json_extract(json_body,'$.float_leg.index_id')));

/********** 4) 評価実行と結果 **********/
CREATE TABLE run (
  run_id TEXT PRIMARY KEY,
  as_of TEXT NOT NULL,
  snapshot_id TEXT NOT NULL REFERENCES market_snapshot(snapshot_id),
  scenario_set_id TEXT,                     -- 将来拡張
  code_hash TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE result (
  run_id TEXT NOT NULL REFERENCES run(run_id),
  trade_id TEXT NOT NULL REFERENCES trade(trade_id),
  measure TEXT NOT NULL,                    -- 'PV'（将来: 'DELTA','DV01' 等）
  bucket TEXT,                              -- 例: '1Y','USD-2Y','K=ATM'
  val REAL NOT NULL,
  ccy TEXT NOT NULL REFERENCES currency(ccy),
  PRIMARY KEY (run_id, trade_id, measure, COALESCE(bucket,''))
);

/********** 5) シナリオ（将来拡張の器） **********/
CREATE TABLE scenario_set (
  scenario_set_id TEXT PRIMARY KEY,
  set_name TEXT NOT NULL,                   -- 旧: name
  description TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE scenario_shock (
  scenario_set_id TEXT NOT NULL REFERENCES scenario_set(scenario_set_id),
  scenario_id INTEGER NOT NULL,             -- 0,1,2...
  target TEXT NOT NULL,                     -- 'CURVE','FX','VOL','MODEL_PARAM'
  target_key TEXT NOT NULL,                 -- 例: 'JPY-OIS','USDJPY','vol_swaption:JPY'
  op_tag TEXT NOT NULL,                     -- 'ADD_BP','MULT','SET'
  shock_val REAL NOT NULL,
  PRIMARY KEY (scenario_set_id, scenario_id, target, target_key, op_tag)
);

/********** 6) 推奨INDEX **********/
CREATE UNIQUE INDEX ux_currency_iso_numeric ON currency(iso_numeric);
