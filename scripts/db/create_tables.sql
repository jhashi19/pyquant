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
--   ('DEFAULT',       'Default',        '通貨の一般業務用のデフォルトカレンダー（非FXのスケジュール生成等）',         100),    -- v1ではこのDEFAULTのみ
--   ('SETTLEMENT',    'Settlement',     '決済用途（FXのSpot/T+N、受渡日、行使・決済日の共通営業日ANDなど）',         100),
--   ('HOLIDAY_ONLY',  'Holiday Only',   '休業日集合のみ参照（営業日ロールには使用しないUI/検証用）',                 200),
--   ('FIXING',        'Rate Fixing',    'Fixing/観測日用途のデフォルトカレンダー（必要になったら利用）',                   150),
--   ('EXCHANGE',      'Exchange',       '上場商品の取引所カレンダー（限月・最終売買日など）',                         150),
--   ('CLEARING',      'Clearing',       '清算機関（CCP）業務日カレンダー（証拠金や清算関連）',                       150),
--   ('DELIVERY',      'Physical Delivery','現物受渡拠点に紐づくカレンダー（コモディティ等で使用）',                  150);


CREATE TABLE m_interp_method (
  interp_method TEXT PRIMARY KEY,  -- 'LOG_LINEAR','LINEAR','CUBIC_SPLINE','MONOTONE_CONVEX_SPLINE'
  description  TEXT,              -- 用途の説明
  created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE m_extrap_method (
  extrap_method TEXT PRIMARY KEY,  -- 'FLAT_FWD','FLAT_ZERO','LINEAR_ZERO'
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
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))   -- 作成UTC
);

CREATE TABLE IF NOT EXISTS currency_calendar (
  ccy        TEXT NOT NULL REFERENCES currency(ccy),
  role       TEXT NOT NULL REFERENCES m_calendar_role(role) ON UPDATE CASCADE ON DELETE RESTRICT,
  cal_id     TEXT NOT NULL REFERENCES calendar_def(cal_id),
  enabled INTEGER NOT NULL DEFAULT 1,   -- 1=有効, 0=無効
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  PRIMARY KEY (ccy, role)                  -- 1通貨×1役割 = 1行を基本とする。v1ではroleはDEFAULTのみ。
);

CREATE TABLE daycount (
  code TEXT PRIMARY KEY,                         -- 'ACT/360','ACT/365F','ACT/ACT-ISDA','30E/360' 等
  display_name TEXT NOT NULL,                    -- 表示名
  formula_tag TEXT NOT NULL UNIQUE,              -- 実装識別子: 'ACT_360','ACT_365F','ACT_ACT_ISDA','THIRTY_E_360' 等
  notes TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE bizday_convention (
  code TEXT PRIMARY KEY,                          -- 'F','P','MF','MP','MFM','NONE'
  display_name TEXT NOT NULL,                     -- 表示名
  rule_tag TEXT NOT NULL,                         -- 'FOLLOWING','PRECEDING','MOD_FOLLOWING','MOD_PRECEDING','NEAREST','NONE'
  nearest_tiebreaker TEXT CHECK(nearest_tiebreaker IN ('PREV','NEXT')), -- NEAREST専用
  notes TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE calendar_def (
  cal_id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  time_zone TEXT NOT NULL,                 -- e.g., 'Asia/Tokyo'
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE market_holiday (
  cal_id TEXT NOT NULL REFERENCES calendar_def(cal_id),
  holiday TEXT NOT NULL,            -- 'YYYY-MM-DD'
  holiday_name TEXT,
  holiday_type TEXT,                -- 'NATIONAL','BANK','MARKET','OBSERVED','AD_HOC'
  observed_of TEXT,                 -- 振替元 'YYYY-MM-DD'
  is_half_day INTEGER NOT NULL DEFAULT 0,  -- v1では0固定
  notes TEXT,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  PRIMARY KEY (cal_id, holiday)
);

CREATE TABLE ref_rate_rule (
  index_id TEXT PRIMARY KEY,                        -- 'USD-SOFR','USD-SOFR-3M','JPY-TONAR' 等に加えて、必要な場合はlookbackやlockoutを含む識別子とする。
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

/********** マーケットデータ受信・監査（ベンダー／受信バッチ） **********/

-- ---------------------------------------------------------------------
-- md_vendor
--
-- テーブル説明:
--   マーケットデータ提供元（ベンダー／フィード元）のマスタ。
-- 使用目的:
--   - 受信データ（md_import_batch）の出所を一意に追跡する
--   - ベンダーの有効/無効を運用管理する
-- 備考:
--   - vendor_id は短い識別子（'BBG','RTRS','QUICK' 等）を想定
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS md_vendor (
  vendor_id     TEXT PRIMARY KEY,                             -- ベンダーID（例: 'BBG','RTRS'）
  display_name  TEXT NOT NULL,                                -- 表示名
  enabled       INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)), -- 1=有効,0=無効
  note          TEXT,                                         -- 補足
  created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX IF NOT EXISTS ix_md_vendor_enabled
  ON md_vendor(enabled);

-- ---------------------------------------------------------------------
-- md_import_batch
--
-- テーブル説明:
--   ベンダー等から受信した「生データ」を、後から再処理・監査・再現できるように
--   1つの処理単位（バッチ）として管理する。
-- 使用目的:
--   - いつ／何を（as_of, cut, domain）受信したかの監査
--   - raw_uri / raw_hash により、同一入力からの再処理（再現性）を可能にする
--   - 受信〜パースの成功/失敗とエラーメッセージを保持する
-- 備考:
--   - raw 本体は DB に格納しない（raw_uri 参照）運用を推奨
--   - as_of（論理日）と received_at（受信時刻）は別概念
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS md_import_batch (
  batch_id      TEXT PRIMARY KEY,                             -- 受信バッチID（例: UUID）
  vendor_id     TEXT NOT NULL REFERENCES md_vendor(vendor_id), -- 出所ベンダー

  data_domain   TEXT NOT NULL                                 -- データ領域（例: 'IR_QUOTE','FX_SPOT','VOL','FIXING','OTHER'）
               CHECK(data_domain IN ('IR_QUOTE','FX_SPOT','VOL','FIXING','OTHER')),

  as_of         TEXT NOT NULL,                                 -- 論理市場日 'YYYY-MM-DD'
  as_of_tz      TEXT NOT NULL DEFAULT 'Asia/Tokyo',            -- as_of の解釈TZ（IANA）
  cut_label     TEXT,                                          -- 運用カット（例: 'EOD'）。不明ならNULL

  received_at   TEXT NOT NULL,                                 -- 受信完了UTC（ISO8601）
  raw_uri       TEXT,                                          -- raw参照先（ファイルパス/オブジェクトキー等）
  raw_hash      TEXT,                                          -- rawハッシュ（SHA等、任意）

  parse_status  TEXT NOT NULL DEFAULT 'RECEIVED'               -- 受信/パース状態
               CHECK(parse_status IN ('RECEIVED','PARSED','FAILED')),
  error_message TEXT,                                          -- parse_status='FAILED' の場合の理由

  note          TEXT,                                          -- 補足
  created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

-- 受信物の探索（as_of×cut×domain）
CREATE INDEX IF NOT EXISTS ix_md_import_batch_asof_cut_domain
  ON md_import_batch(as_of, cut_label, data_domain);

-- ベンダー別棚卸し
CREATE INDEX IF NOT EXISTS ix_md_import_batch_vendor_asof
  ON md_import_batch(vendor_id, as_of);

-- 当日受信分の追跡
CREATE INDEX IF NOT EXISTS ix_md_import_batch_received_at
  ON md_import_batch(received_at);


/********** 2) マーケット・スナップショット **********/
CREATE TABLE market_snapshot (
  snapshot_id         TEXT PRIMARY KEY,                        -- 例: UUID
  as_of               TEXT NOT NULL,                           -- 論理市場日 'YYYY-MM-DD'
  as_of_tz            TEXT NOT NULL DEFAULT 'Asia/Tokyo',      -- 市場日の解釈TZ（IANA）。起算日→現地日を決める
  cut_label           TEXT,                                     -- 'EOD','NY_10AM','LDN_4PM' 等の運用カット名
  data_hash           TEXT NOT NULL,                            -- 入力一式の総ハッシュ（再現性同定）
  parent_snapshot_id  TEXT REFERENCES market_snapshot(snapshot_id), -- 親版（差分や派生元）
  qa_status           TEXT NOT NULL DEFAULT 'PENDING' CHECK(qa_status IN ('PENDING','APPROVED','REJECTED')), -- 品質審査状態
  approved_by         TEXT,                                     -- 承認したユーザ
  approved_at         TEXT,                                     -- 承認UTC時刻
  is_locked           INTEGER NOT NULL DEFAULT 0,               -- 1=ロック済（以後不変の想定）
  locked_by           TEXT,                                     -- 当版をロックしたユーザ
  locked_at           TEXT,                                     -- 当版をロックしたUTC時刻（EOD確定の時刻）
  note                TEXT,                                     -- 補足
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))    -- 生成UTC
);
CREATE UNIQUE INDEX ux_market_snapshot_hash ON market_snapshot(data_hash);
CREATE INDEX        ix_market_snapshot_asof ON market_snapshot(as_of, cut_label);
CREATE INDEX        ix_market_snapshot_parent ON market_snapshot(parent_snapshot_id);

/* market_snapshot: UI needs "latest locked snapshot" selection */
CREATE INDEX IF NOT EXISTS ix_market_snapshot_locked_cut_asof_desc
  ON market_snapshot (cut_label, as_of DESC, locked_at DESC)
  WHERE is_locked = 1;

/* market_snapshot: UI needs QA workflow lists (PENDING/APPROVED/REJECTED) */
CREATE INDEX IF NOT EXISTS ix_market_snapshot_qa_status_created
  ON market_snapshot (qa_status, created_at DESC);

-- ---------------------------------------------------------------------
-- market_snapshot_input
--
-- テーブル説明:
--   1つの market_snapshot が、どの md_import_batch（受信バッチ）群から構成されたかを表す関連。
-- 使用目的:
--   - snapshot の再現性確保（「この snapshot は何の受信物を使ったか」）
--   - 障害解析/監査（IR/FX/VOL/FIXING など各ドメインの元入力を特定）
-- 備考:
--   - role は snapshot 内での当該バッチの用途（domain相当）を表す
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS market_snapshot_input (
  snapshot_id   TEXT NOT NULL REFERENCES market_snapshot(snapshot_id) ON DELETE CASCADE,
  batch_id      TEXT NOT NULL REFERENCES md_import_batch(batch_id) ON DELETE RESTRICT,

  role          TEXT NOT NULL
               CHECK(role IN ('IR_QUOTE','FX_SPOT','VOL','FIXING','OTHER')), -- snapshot内での役割
  note          TEXT,                                         -- 補足
  created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),

  PRIMARY KEY (snapshot_id, role)
);

CREATE INDEX IF NOT EXISTS ix_market_snapshot_input_batch
  ON market_snapshot_input(batch_id);


CREATE TABLE fx_spot (
  snapshot_id      TEXT NOT NULL REFERENCES market_snapshot(snapshot_id),
  base_ccy         TEXT NOT NULL REFERENCES currency(ccy),   -- レートの分子通貨（例: USD）
  quote_ccy        TEXT NOT NULL REFERENCES currency(ccy),   -- レートの分母通貨（例: JPY）
  pair             TEXT NOT NULL,                            -- 'USDJPY'
  /* base_ccy||quote_ccy と一致させて整合を保証 */
  spot             REAL NOT NULL,                            -- MID（= (bid+ask)/2 を推奨）
  bid              REAL,                                     -- 片サイド（任意）
  ask              REAL,                                     -- 片サイド（任意）
  frozen_at   TEXT,                                     -- このペア固有の観測UTC時刻（任意）
  source_symbol    TEXT,                                     -- ベンダ銘柄/ティッカー（任意）

  /* 追加：クロス導出フラグ（ベンダ直物で無い場合を明示） */
  is_cross_derived INTEGER NOT NULL DEFAULT 0,               -- 1=クロス導出（例: EURJPY = EURUSD*USDJPY）
  derived_via_1    TEXT,                                     -- 由来ペア1（例: 'EURUSD'）
  derived_via_2    TEXT,                                     -- 由来ペア2（例: 'USDJPY'）
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),

  CHECK (pair = base_ccy || quote_ccy),
  CHECK ( (bid IS NULL AND ask IS NULL) OR (bid <= ask) ),
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
  /* 曲線“座標”の年率化と複利慣行（ゼロ↔DF 変換に使用） */
  daycount      TEXT NOT NULL REFERENCES daycount(code),
  compounding   TEXT NOT NULL DEFAULT 'CONTINUOUS'
                CHECK (compounding IN ('CONTINUOUS','SIMPLE','DISCRETE')),
  compounding_freq INTEGER,                                       -- compounding='DISCRETE' のとき必須（例: 1=annual,2=semi annual,4=quarterly,12=monthly,365=daily）

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

  CHECK ( (curve_type='FORECAST' AND ref_rate_id IS NOT NULL)
       OR (curve_type!='FORECAST' AND ref_rate_id IS NULL) ),
  CHECK (valid_to IS NULL OR valid_to >= valid_from)
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_pricing_curve_def_key
  ON pricing_curve_def (ccy, curve_type, IFNULL(ref_rate_id,''), valid_to);

CREATE INDEX IF NOT EXISTS ix_pricing_curve_def_ccy_type
  ON pricing_curve_def (ccy, curve_type);

CREATE TABLE curve_point (
  snapshot_id   TEXT NOT NULL REFERENCES market_snapshot(snapshot_id),
  curve_id      TEXT NOT NULL REFERENCES pricing_curve_def(curve_id),

  /* ピラーの指定。 */
  pillar_kind   TEXT NOT NULL CHECK (pillar_kind IN ('TENOR','DATE')),
  pillar_key    TEXT NOT NULL,
  tenor         TEXT,                         -- 例: '1D','1W','1M','6M','1Y','10Y'
  pillar_date   TEXT,                         -- 例: '2026-03-31'（DATE モード）

  /* 座標：評価起点 as_of からの年率時間（pricing_curve_def.daycount に基づく） */
  x_years       REAL NOT NULL,                -- 例: 0.5 (= 約半年)。補間・外挿の独立変数

  /* 値：DF と連続複利ゼロ（どちらか必須。両方があれば整合チェックが容易） */
  df            REAL,                         -- ディスカウントファクター
  zero_cont     REAL,                         -- 連続複利ゼロレート r（年率）

  /* 監査用メタ */
  is_extrapolated INTEGER NOT NULL DEFAULT 0, -- 1=外挿点（ピラー外の点）
  pillar_source   TEXT NOT NULL DEFAULT 'RAW'
                    CHECK (pillar_source IN ('RAW','BOOTSTRAP','DERIVED','SMOOTHED')),
  source_symbol   TEXT,                       -- ベンダの銘柄/コード等（任意）
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),   -- 取込UTC

  CHECK (df IS NOT NULL OR zero_cont IS NOT NULL),
  CHECK (
    (pillar_kind='TENOR' AND tenor IS NOT NULL AND pillar_date IS NULL AND pillar_key = tenor)
    OR
    (pillar_kind='DATE'  AND pillar_date IS NOT NULL AND tenor IS NULL AND pillar_key = pillar_date)
  ),

  /* 主キー：同一スナップショット＋カーブで、ピラー一意（TENOR/DATE 併用を許容） */
  PRIMARY KEY (snapshot_id, curve_id, pillar_kind, pillar_key)
);

/* curve_point: UI needs to display curve points for a snapshot+curve ordered by x_years */
CREATE INDEX IF NOT EXISTS ix_curve_point_snapshot_curve_x
  ON curve_point (snapshot_id, curve_id, x_years);

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
  settlement_bdc     TEXT REFERENCES bizday_convention(code),  -- 決済日の営業日調整規則（未指定なら coupon_bdc 等を優先採用）
  settlement_cal_id  TEXT REFERENCES calendar_def(cal_id),     -- 決済日の判定に用いるカレンダー（未指定なら coupon_cal_id 等を優先採用）
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


/* FX インプライド・ボラティリティ（Garman–Kohlhagen 等で使用） */
CREATE TABLE vol_fx (
  /* 識別 */
  vol_id         TEXT PRIMARY KEY,                               -- 例: UUID（複合キーの煩雑さを避ける）
  snapshot_id    TEXT NOT NULL REFERENCES market_snapshot(snapshot_id),

  /* 通貨ペア（fx_spot と同じ正規化） */
  base_ccy       TEXT NOT NULL REFERENCES currency(ccy),         -- 分子通貨（例: USD）
  quote_ccy      TEXT NOT NULL REFERENCES currency(ccy),         -- 分母通貨（例: JPY）
  pair           TEXT NOT NULL,                                  -- 'USDJPY'
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
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),  -- 取込UTC

  CHECK (pair = base_ccy || quote_ccy),

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
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),    -- 取込UTC

  CHECK (
    (smile_type='ATM'    AND strike_rate IS NULL)
    OR
    (smile_type='STRIKE' AND strike_rate IS NOT NULL)
  )
);


CREATE UNIQUE INDEX IF NOT EXISTS ux_vol_capfloor_uq
ON vol_capfloor(
  snapshot_id, ccy,
  COALESCE(expiry_date,''), COALESCE(expiry_tenor,''),
  index_tenor, smile_type,
  COALESCE(strike_rate,-1.0), quote_type
);


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
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);


CREATE UNIQUE INDEX IF NOT EXISTS ux_vol_swaption_uq
ON vol_swaption(
  snapshot_id, ccy,
  COALESCE(expiry_date,''), COALESCE(expiry_tenor,''),
  swap_tenor, quote_type
);

/* 過去のFixing情報 */
CREATE TABLE historical_fixing (
  index_id       TEXT NOT NULL REFERENCES ref_rate_rule(index_id),
  fixing_date    TEXT NOT NULL,          -- 観測日
  rate           REAL NOT NULL,          -- 確定レート
  source_symbol  TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  PRIMARY KEY (index_id, fixing_date)
);

/* historical_fixing: UI needs to list fixings by date across indices */
CREATE INDEX IF NOT EXISTS ix_historical_fixing_date
  ON historical_fixing (fixing_date, index_id);

/********** 2.x) マーケットクォート（ブートストラップ入力） **********/

-- ---------------------------------------------------------------------
-- market_quote_hdr
--
-- テーブル説明:
--   ブートストラップ入力となる「正規化済みクォート」の共通ヘッダ。
-- 使用目的:
--   - snapshot 内で採用したクォート集合を一意に保持する（use_in_build）
--   - QA（qa_flag）・出所（vendor/source_symbol/batch）・受信時刻（recv_ts）を共通管理する
-- 備考:
--   - quote_type ごとに詳細テーブル（deposit/futures/swap 等）へ 1:1 でぶら下げる
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS market_quote_hdr (
  quote_id      TEXT PRIMARY KEY,                             -- クォートID（例: UUID）
  snapshot_id   TEXT NOT NULL REFERENCES market_snapshot(snapshot_id), -- 属するマーケットスナップショット
  batch_id      TEXT REFERENCES md_import_batch(batch_id),     -- 由来受信バッチ（任意）
  vendor_id     TEXT REFERENCES md_vendor(vendor_id),          -- 由来ベンダー（任意：batch_id から導出できる場合も）
  quote_type    TEXT NOT NULL                                  -- クォート種別（v1: DEPOSIT/IR_FUTURES/SWAP/BOND）
               CHECK(quote_type IN ('DEPOSIT','IR_FUTURES','SWAP','BOND')),

  source_symbol TEXT,                                         -- ベンダー側ティッカー/ID（任意）
  recv_ts       TEXT,                                         -- 当該クォートの観測/受信UTC（任意：ティック時刻等）

  qa_flag       TEXT NOT NULL DEFAULT 'OK'
               CHECK(qa_flag IN ('OK','SUSPECT','BAD')),       -- 品質フラグ（外れ値等）
  use_in_build  INTEGER NOT NULL DEFAULT 1 CHECK(use_in_build IN (0,1)), -- 1=採用,0=除外（手動除外など）
  note          TEXT,                                         -- 補足

  created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX IF NOT EXISTS ix_market_quote_hdr_snapshot_type_use
  ON market_quote_hdr(snapshot_id, quote_type, use_in_build, qa_flag);

CREATE INDEX IF NOT EXISTS ix_market_quote_hdr_batch
  ON market_quote_hdr(batch_id);

-- ---------------------------------------------------------------------
-- market_quote_deposit
--
-- テーブル説明:
--   Deposit/短期金利（ON/TN/1W/1M 等）のクォート詳細。
-- 使用目的:
--   - ブートストラップの短期端（DF確定）の入力として使用する
-- 備考:
--   - rate_* は年率を 0.01=1% のような小数で格納する
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS market_quote_deposit (
  quote_id      TEXT PRIMARY KEY
               REFERENCES market_quote_hdr(quote_id) ON DELETE CASCADE,

  ccy           TEXT NOT NULL REFERENCES currency(ccy),
  tenor         TEXT NOT NULL,                                 -- 例: 'ON','TN','1W','1M'
  start_date    TEXT,                                          -- 任意（特殊開始の記録が必要な場合）
  end_date      TEXT,                                          -- 任意

  daycount      TEXT NOT NULL REFERENCES daycount(code),        -- 例: 'ACT/360'
  bdc           TEXT REFERENCES bizday_convention(code),        -- 営業日規則（任意）
  cal_id        TEXT REFERENCES calendar_def(cal_id),           -- カレンダー（任意）

  rate_mid      REAL NOT NULL,                                 -- MID年率（0.01=1%）
  rate_bid      REAL,                                          -- 任意
  rate_ask      REAL,                                          -- 任意

  created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),

  CHECK ( (rate_bid IS NULL AND rate_ask IS NULL) OR (rate_bid <= rate_ask) )
);

CREATE INDEX IF NOT EXISTS ix_market_quote_deposit_ccy_tenor
  ON market_quote_deposit(ccy, tenor);

-- ---------------------------------------------------------------------
-- market_quote_ir_futures
--
-- テーブル説明:
--   金利先物クォート詳細（例: 3M SOFR futures 等）。
-- 使用目的:
--   - 先物価格からフォワードレートを導出し、曲線構築の中間端入力に使用する
-- 備考:
--   - 銘柄仕様は ir_futures_def に集約し、ここは「限月×価格」を保持する
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS market_quote_ir_futures (
  quote_id      TEXT PRIMARY KEY
               REFERENCES market_quote_hdr(quote_id) ON DELETE CASCADE,

  fut_code      TEXT NOT NULL REFERENCES ir_futures_def(fut_code), -- 銘柄
  contract_month TEXT NOT NULL,                                    -- 限月 'YYYYMM'（例: '202603'）

  price_mid     REAL NOT NULL,                                    -- MID価格（銘柄仕様により 100-価格型 等）
  price_bid     REAL,                                             -- 任意
  price_ask     REAL,                                             -- 任意

  settle_date   TEXT,                                             -- 任意（清算日等を記録したい場合）
  created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),

  CHECK ( (price_bid IS NULL AND price_ask IS NULL) OR (price_bid <= price_ask) )
);

CREATE INDEX IF NOT EXISTS ix_market_quote_ir_fut_code_expiry
  ON market_quote_ir_futures(fut_code, contract_month);

-- ---------------------------------------------------------------------
-- market_quote_swap
--
-- テーブル説明:
--   Swap（固定 vs 変動）クォート詳細（パーレート）。
-- 使用目的:
--   - 中長期端のブートストラップ入力として使用する（固定レート=パーレート）
-- 備考:
--   - 変動側参照金利は ref_rate_rule(index_id) を参照し、OIS/TERM の差異は ref_rate_rule 側で表現する
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS market_quote_swap (
  quote_id        TEXT PRIMARY KEY
                 REFERENCES market_quote_hdr(quote_id) ON DELETE CASCADE,

  ccy             TEXT NOT NULL REFERENCES currency(ccy),
  float_index_id  TEXT NOT NULL REFERENCES ref_rate_rule(index_id), -- 例: 'JPY-TONAR','USD-SOFR-3M' 等

  maturity_tenor  TEXT NOT NULL,                                 -- 例: '1Y','2Y','10Y'
  effective_date  TEXT,                                          -- 任意（必要な場合のみ記録）
  maturity_date   TEXT,                                          -- 任意

  fixed_rate_mid  REAL NOT NULL,                                 -- パーレート（0.01=1%）
  fixed_rate_bid  REAL,
  fixed_rate_ask  REAL,
  -- 固定脚規約
  fixed_freq      TEXT NOT NULL,                                 -- 例: '1Y','6M'
  fixed_daycount  TEXT NOT NULL REFERENCES daycount(code),
  fixed_bdc       TEXT REFERENCES bizday_convention(code),
  fixed_cal_id    TEXT REFERENCES calendar_def(cal_id),

  -- 変動脚規約
  float_freq      TEXT NOT NULL,                                 -- 例: '3M'（OISなら 'ON' 等）
  float_daycount  TEXT NOT NULL REFERENCES daycount(code),
  float_bdc       TEXT REFERENCES bizday_convention(code),
  float_cal_id    TEXT REFERENCES calendar_def(cal_id),

  spot_lag_days   INTEGER NOT NULL DEFAULT 2,                    -- 開始日スポットラグ（日数）
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),

  CHECK ( (fixed_rate_bid IS NULL AND fixed_rate_ask IS NULL) OR (fixed_rate_bid <= fixed_rate_ask) )
);

CREATE INDEX IF NOT EXISTS ix_market_quote_swap_ccy_maturity
  ON market_quote_swap(ccy, maturity_tenor);

-- =========================================================
-- BOND Quote
--   共通ヘッダ: market_quote_hdr（snapshot_id / quote_type / qa_flag / use_in_build 等）
--   明細:      market_quote_bond
-- =========================================================

CREATE TABLE IF NOT EXISTS market_quote_bond (
  quote_id      TEXT PRIMARY KEY
               REFERENCES market_quote_hdr(quote_id) ON DELETE CASCADE,

  security_id   TEXT NOT NULL
               REFERENCES bond_def(security_id),              -- 債券銘柄

  -- 価格/利回り（いずれか1つ以上は必須）
  clean_price_mid  REAL,                                      -- クリーン価格 MID（例: 100.32）
  clean_price_bid  REAL,                                      -- クリーン価格 BID（任意）
  clean_price_ask  REAL,                                      -- クリーン価格 ASK（任意）

  dirty_price_mid  REAL,                                      -- ダーティ価格 MID（任意）
  dirty_price_bid  REAL,                                      -- ダーティ価格 BID（任意）
  dirty_price_ask  REAL,                                      -- ダーティ価格 ASK（任意）

  yield_to_mty_mid REAL,                                      -- 最終利回り(YTM) MID（例: 0.0125 = 1.25%）
  yield_to_mty_bid REAL,                                      -- YTM BID（任意）
  yield_to_mty_ask REAL,                                      -- YTM ASK（任意）

  quote_ccy     TEXT REFERENCES currency(ccy),                -- 任意：配信通貨メタ（通常は bond_def.ccy と一致）
  settle_date   TEXT,                                         -- 任意：当該クォートの決済日(T+N)を固定して持ちたい場合

  created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),

  -- bid/ask の整合（片側NULLは許容。両方あるときのみ bid<=ask を要求）
  CHECK ( (clean_price_bid IS NULL AND clean_price_ask IS NULL) OR (clean_price_bid <= clean_price_ask) ),
  CHECK ( (dirty_price_bid IS NULL AND dirty_price_ask IS NULL) OR (dirty_price_bid <= dirty_price_ask) ),
  CHECK ( (yield_to_mty_bid IS NULL AND yield_to_mty_ask IS NULL) OR (yield_to_mty_bid <= yield_to_mty_ask) ),

  -- 少なくともどれかの MID が入っていること（価格のみ／利回りのみ配信に対応）
  CHECK (
    clean_price_mid  IS NOT NULL
    OR dirty_price_mid IS NOT NULL
    OR yield_to_mty_mid IS NOT NULL
  )
);

-- 参照頻度が高い想定の補助インデックス（任意）
CREATE INDEX IF NOT EXISTS idx_market_quote_bond_security
  ON market_quote_bond (security_id);

/********** カーブ構築監査（ブートストラップ実行・フィット） **********/

-- ---------------------------------------------------------------------
-- curve_build_run
--
-- テーブル説明:
--   snapshot_id × curve_id ごとの「カーブ構築実行」を管理する。
-- 使用目的:
--   - 成功/失敗、入力ハッシュ、アルゴリズムバージョン、実行時刻を保持して再現性を確保する
-- 備考:
--   - 原則 snapshot_id×curve_id は 1回で確定（やり直しは新snapshotで実施）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS curve_build_run (
  build_id      TEXT PRIMARY KEY,                                -- 実行ID（UUID）
  snapshot_id   TEXT NOT NULL REFERENCES market_snapshot(snapshot_id),
  curve_id      TEXT NOT NULL REFERENCES pricing_curve_def(curve_id),

  build_status  TEXT NOT NULL DEFAULT 'RUNNING'
               CHECK(build_status IN ('RUNNING','SUCCESS','FAILED')),

  input_hash    TEXT NOT NULL,                                   -- 採用クォート集合等から算出（同一性検証）
  algo_version  TEXT,                                            -- 実装バージョン（Git hash 等、任意）

  started_at    TEXT,                                            -- 実行開始UTC
  finished_at   TEXT,                                            -- 実行終了UTC
  error_message TEXT,                                            -- build_status='FAILED' の理由

  created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),

  UNIQUE (snapshot_id, curve_id)
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
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
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
  logical_trade_id TEXT NOT NULL,         -- 同一ロジカル取引（同一Deal）の固定ID：全バージョンで共通
  product        TEXT NOT NULL REFERENCES m_trade_product(product),
  portfolio_id   TEXT REFERENCES portfolio(portfolio_id),
  ccy            TEXT NOT NULL REFERENCES currency(ccy),          -- 報告/換算通貨
  notional       REAL NOT NULL,
  buy_sell       TEXT NOT NULL CHECK (buy_sell IN ('buy','sell')),  -- 単レグ商品の早見符号
  trade_date      TEXT NOT NULL,
  effective_date  TEXT,
  maturity_date   TEXT,
  status          TEXT CHECK (status IN (
                      'NEW','AMENDED','CANCELLED','EXERCISED','EXPIRED','NOVATED')),
  version_no      INTEGER,         -- バージョン番号（同一 logical_trade_id 内で単調増加）
  parent_trade_id TEXT REFERENCES trade(trade_id),       -- 直前バージョン（チェーンの前方向）。ルートは NULL
  replaced_by_trade_id TEXT REFERENCES trade(trade_id),  -- 直後バージョン（チェーンの後方向）。最新は NULL
  is_current       INTEGER NOT NULL DEFAULT 1,            -- 評価対象か（キャンセル/行使/失効などは 0 を想定）
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
  valid_from      TEXT NOT NULL,    -- 'YYYY-MM-DD'（日次）で統一し、run.as_of と同粒度で比較する。有効期間の開始。
  valid_to        TEXT,             -- 'YYYY-MM-DD'（日次）で統一し、run.as_of と同粒度で比較する。有効期間の終了。
  updated_at      TEXT,
  closed_at       TEXT,
  canceled_at     TEXT,
  source_tag      TEXT,
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),

  /* ルートは logical_trade_id=trade_id を必須化（ロジカルIDの起点を固定） */
  CHECK (parent_trade_id IS NOT NULL OR logical_trade_id = trade_id),
  CHECK (valid_to IS NULL OR valid_from < valid_to)
);


/* trade: version/history */
CREATE UNIQUE INDEX IF NOT EXISTS ux_trade_logical_version
  ON trade (logical_trade_id, version_no);

/* 1ロジカル取引につき「valid_to=NULL（＝最新）」は高々1行 */
CREATE UNIQUE INDEX IF NOT EXISTS ux_trade_logical_open
  ON trade (logical_trade_id)
  WHERE valid_to IS NULL;

/* チェーン追跡・履歴表示を高速化 */
CREATE INDEX IF NOT EXISTS idx_trade_parent
  ON trade (parent_trade_id);

CREATE INDEX IF NOT EXISTS idx_trade_replaced_by
  ON trade (replaced_by_trade_id);

/* 取引一覧（原則：最新バージョンのみ） */
CREATE INDEX IF NOT EXISTS idx_trade_latest_portfolio_trade_date
  ON trade (portfolio_id, trade_date, trade_id)
  WHERE valid_to IS NULL;

CREATE INDEX IF NOT EXISTS idx_trade_latest_portfolio_maturity_date
  ON trade (portfolio_id, maturity_date, trade_id)
  WHERE valid_to IS NULL;

/* フィルタ（ポートフォリオ×商品×通貨×ステータス） */
CREATE INDEX IF NOT EXISTS idx_trade_latest_portfolio_product_ccy_status
  ON trade (portfolio_id, product, ccy, status, trade_id)
  WHERE valid_to IS NULL;



CREATE TRIGGER IF NOT EXISTS trg_trade_version_link
AFTER INSERT ON trade
WHEN NEW.parent_trade_id IS NOT NULL
BEGIN
  /* 直前バージョンをクローズして後継リンクを張る */
  UPDATE trade
    SET replaced_by_trade_id = NEW.trade_id,
         valid_to            = NEW.valid_from,
         updated_at          = (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
   WHERE trade_id = NEW.parent_trade_id;

  /* 親が存在しない/既に後継がいる等の異常を弾く */
  SELECT RAISE(ABORT, 'invalid parent_trade_id or branching')
   WHERE NOT EXISTS (SELECT 1 FROM trade p WHERE p.trade_id = NEW.parent_trade_id);

  SELECT RAISE(ABORT, 'parent already has replaced_by_trade_id')
   WHERE EXISTS (
     SELECT 1 FROM trade p
      WHERE p.trade_id = NEW.parent_trade_id
        AND p.replaced_by_trade_id IS NOT NULL
        AND p.replaced_by_trade_id <> NEW.trade_id
   );

  /* logical_trade_id は親と同一であること（チェーンの一貫性） */
  SELECT RAISE(ABORT, 'logical_trade_id must match parent')
   WHERE (SELECT logical_trade_id FROM trade WHERE trade_id = NEW.parent_trade_id) <> NEW.logical_trade_id;

  /* version_no は親+1（親がNULL等のケースはアプリで明示的に制御） */
  SELECT RAISE(ABORT, 'version_no must be parent.version_no + 1')
   WHERE (SELECT version_no FROM trade WHERE trade_id = NEW.parent_trade_id) + 1 <> NEW.version_no;
END;

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
  notional_ratio REAL NOT NULL CHECK (notional_ratio > 0.0 AND notional_ratio <= 1.0),  -- 初期 notional に対する比率 (0.0〜1.0) 例: 0.8, 0.6, など

  created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  PRIMARY KEY (trade_id, step_no),
  UNIQUE (trade_id, change_date)    -- 1 つの IRS で同じ change_date を重複登録しない
);


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
  clean_price_agreed REAL NOT NULL,                                 -- 約定単価（clean price）
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
  ref_rate_id        TEXT NOT NULL REFERENCES ref_rate_rule(index_id), -- 観測指標（例: 'USD-SOFR-3M','JPY-TONAR-3M'）
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

CREATE TABLE IF NOT EXISTS swap_schedule (
  trade_id        TEXT NOT NULL REFERENCES trade(trade_id) ON DELETE CASCADE,
  leg_id          TEXT NOT NULL,
  cashflow_no     INTEGER NOT NULL,
  payment_date    TEXT NOT NULL,
  payment_type    TEXT NOT NULL CHECK (payment_type IN ('INTEREST','PRINCIPAL','FEE')),

  pay_rec         TEXT NOT NULL CHECK (pay_rec IN ('PAY','REC')),
  ccy             TEXT NOT NULL REFERENCES currency(ccy),

  start_date      TEXT,
  end_date        TEXT,
  daycount        TEXT REFERENCES daycount(code),
  accrual_factor  REAL,

  notional        REAL,
  principal_factor REAL,

  index_id        TEXT REFERENCES ref_rate_rule(index_id),
  spread          REAL,
  gearing         REAL DEFAULT 1.0,
  rate_calc_type  TEXT CHECK (rate_calc_type IN ('FIXED','IBOR_SINGLE','OIS_COMPOUNDED','OIS_AVERAGED','MANUAL')),

  fixing_date     TEXT,
  obs_start_date  TEXT,
  obs_end_date    TEXT,

  rate            REAL,
  amount          REAL,
  fixed_amount    REAL,

  settled_amount  REAL,
  is_settled      INTEGER NOT NULL DEFAULT 0 CHECK (is_settled IN (0,1)),
  settled_date    TEXT,
  settlement_ref  TEXT,

  updated_at      TEXT,
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),

  CHECK (
    payment_type <> 'INTEREST'
    OR (
      start_date IS NOT NULL
      AND end_date IS NOT NULL
      AND daycount IS NOT NULL
      AND accrual_factor IS NOT NULL
      AND notional IS NOT NULL
    )
  ),

  PRIMARY KEY (trade_id, leg_id, cashflow_no)
);


CREATE INDEX IF NOT EXISTS idx_swap_schedule_trade_leg
  ON swap_schedule(trade_id, leg_id);

CREATE INDEX IF NOT EXISTS idx_swap_schedule_trade_leg_paydate
  ON swap_schedule(trade_id, leg_id, payment_date);

CREATE INDEX IF NOT EXISTS idx_swap_schedule_index_fixing
  ON swap_schedule(index_id, fixing_date);

CREATE INDEX IF NOT EXISTS idx_swap_schedule_payment_date
  ON swap_schedule(payment_date);

CREATE INDEX IF NOT EXISTS idx_swap_schedule_unsettled_date
  ON swap_schedule(is_settled, payment_date);


CREATE TABLE IF NOT EXISTS bond_schedule (
  security_id          TEXT NOT NULL,
  base_security_id     TEXT NOT NULL REFERENCES bond_def(security_id) ON DELETE CASCADE,
  trade_id             TEXT REFERENCES trade(trade_id) ON DELETE CASCADE,

  cashflow_no          INTEGER NOT NULL,
  payment_date         TEXT NOT NULL,
  payment_type         TEXT NOT NULL CHECK (payment_type IN ('INTEREST','PRINCIPAL')),
  ccy                  TEXT NOT NULL REFERENCES currency(ccy),

  start_date           TEXT,
  end_date             TEXT,
  daycount             TEXT REFERENCES daycount(code),
  accrual_factor        REAL,

  base_notional        REAL NOT NULL DEFAULT 100.0 CHECK (base_notional > 0.0),
  notional_factor      REAL NOT NULL DEFAULT 1.0 CHECK (notional_factor > 0.0 AND notional_factor <= 1.0),
  principal_factor     REAL NOT NULL DEFAULT 0.0 CHECK (principal_factor >= 0.0 AND principal_factor <= 1.0),

  -- 金利決定（固定債/変動債を同一表で扱うための共通項目）
  rate_calc_type       TEXT NOT NULL CHECK (rate_calc_type IN ('FIXED','IBOR_SINGLE','OIS_COMPOUNDED','OIS_AVERAGED','MANUAL')),
  index_id             TEXT REFERENCES ref_rate_rule(index_id),
  spread               REAL,
  gearing              REAL DEFAULT 1.0,
  fixing_date          TEXT,
  obs_start_date       TEXT,
  obs_end_date         TEXT,

  rate                 REAL,
  amount_per_base      REAL,
  fixed_amount_per_base REAL,

  is_stub              INTEGER NOT NULL DEFAULT 0 CHECK (is_stub IN (0,1)),
  updated_at           TEXT,
  created_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),

  CHECK (
    payment_type <> 'INTEREST'
    OR (
      start_date IS NOT NULL
      AND end_date IS NOT NULL
      AND daycount IS NOT NULL
      AND accrual_factor IS NOT NULL
    )
  ),

  PRIMARY KEY (security_id, cashflow_no)
);

CREATE INDEX IF NOT EXISTS idx_bond_schedule_security_payment
  ON bond_schedule(security_id, payment_date);

CREATE INDEX IF NOT EXISTS idx_bond_schedule_base_security_payment
  ON bond_schedule(base_security_id, payment_date);

CREATE INDEX IF NOT EXISTS idx_bond_schedule_trade_id
  ON bond_schedule(trade_id);


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


/* run が参照した trade バージョンを固定（materialize）して保持 */
CREATE TABLE IF NOT EXISTS run_trade (
  run_id          TEXT NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
  trade_id        TEXT NOT NULL REFERENCES trade(trade_id),
  logical_trade_id TEXT NOT NULL,
  version_no      INTEGER NOT NULL,
  created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  PRIMARY KEY (run_id, trade_id),
  /* 1run内では 1ロジカル取引につき1バージョンのみ */
  UNIQUE (run_id, logical_trade_id)
);

CREATE INDEX IF NOT EXISTS idx_run_trade_trade
  ON run_trade(trade_id);

/* run ごとの対象ポートフォリオ */
CREATE TABLE IF NOT EXISTS run_portfolio (
  run_id  TEXT NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
  portfolio_id TEXT NOT NULL REFERENCES portfolio(portfolio_id),
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  PRIMARY KEY (run_id, portfolio_id)
);
CREATE INDEX IF NOT EXISTS idx_run_portfolio_portfolio
  ON run_portfolio(portfolio_id);

/* run ごとの対象メジャー */
CREATE TABLE IF NOT EXISTS run_measure (
  run_id     TEXT NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
  measure_id TEXT NOT NULL REFERENCES measure_def(measure_id),
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  PRIMARY KEY (run_id, measure_id)
);

/* run ごとの債券キャリブレーション状態（z-spread/経過利息など）
   - 債券PV計算時に「市場クォートにフィットする z-spread」を一度だけ校正し、
     以降（感応度・PL分解など）の計算では再校正せず入力として再利用するためのキャッシュ。
   - 粒度は run × security × discount_curve × settle_date。
*/
CREATE TABLE IF NOT EXISTS run_bond_pricing_state (
  run_id            TEXT NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
  security_id       TEXT NOT NULL REFERENCES bond_def(security_id),

  -- pricing_profile_map(md_role='DISCOUNT_CURVE') で解決された割引カーブ（curve_id）
  discount_curve_id TEXT NOT NULL REFERENCES pricing_curve_def(curve_id),

  -- 評価上の決済日（通常は as_of の T+N を暦調整した日）
  settle_date       TEXT NOT NULL,                               -- 'YYYY-MM-DD'

  -- 参照した市場クォート（トレース用）。NULL可（手入力・テスト等）
  quote_id          TEXT REFERENCES market_quote_hdr(quote_id) ON DELETE SET NULL,

  -- どの観測値にフィットしたか（価格は額面100あたり、YTMは年率の実数表現）
  price_kind        TEXT NOT NULL CHECK (price_kind IN ('CLEAN','DIRTY')),
  input_side        TEXT NOT NULL DEFAULT 'MID' CHECK (input_side IN ('MID','BID','ASK')),
  price_value       REAL NOT NULL,

  -- 価格通貨メタ（通常は bond_def.ccy と一致）。トレース用途で任意
  price_ccy         TEXT REFERENCES currency(ccy),

  -- 経過利息（settle_date 基準、額面100あたり）
  accrued_interest  REAL NOT NULL,

  -- 参照・デバッグ用：入力から整合変換した値（NULL可）
  obs_clean_price   REAL,                                        -- 額面100あたり
  obs_dirty_price   REAL,                                        -- 額面100あたり
  obs_ytm           REAL,                                        -- 年率（実数表現）

  -- 校正結果：z-spread（年率の実数表現。例: 12bp = 0.0012）
  z_spread          REAL NOT NULL,

  -- z-spread の適用規約（DFシフトに使用）
  z_spread_daycount     TEXT NOT NULL REFERENCES daycount(code),
  z_spread_compounding  TEXT NOT NULL DEFAULT 'CONTINUOUS'
                      CHECK (z_spread_compounding IN ('CONTINUOUS','SIMPLE','DISCRETE')),
  z_spread_compounding_freq INTEGER,                             -- DISCRETE のとき必須（例: 1,2,4,12,365）

  created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),

  CHECK ( (z_spread_compounding != 'DISCRETE') OR (z_spread_compounding_freq IS NOT NULL) ),

  PRIMARY KEY (run_id, security_id, discount_curve_id, settle_date)
);

CREATE INDEX IF NOT EXISTS idx_run_bond_pricing_state_security
  ON run_bond_pricing_state (security_id);

CREATE INDEX IF NOT EXISTS idx_run_bond_pricing_state_quote
  ON run_bond_pricing_state (quote_id);

CREATE INDEX IF NOT EXISTS idx_run_bond_pricing_state_curve
  ON run_bond_pricing_state (discount_curve_id);


/* =========================
   評価結果
   ========================= */

/* 日次評価結果（PV/日次PL 等の基本結果） */
CREATE TABLE result_eod (
  run_id       TEXT NOT NULL REFERENCES run(run_id),
  trade_id     TEXT NOT NULL REFERENCES trade(trade_id),

  pl_type TEXT DEFAULT 'HPL' CHECK (pl_type IN ('APL','HPL')),  -- 'APL'=実損益, 'HPL'=仮想損益

  -- この結果の通貨（少なくとも「集計・表示する通貨」を固定する）
  ccy          TEXT NOT NULL REFERENCES currency(ccy),

  base_run_id  TEXT REFERENCES run(run_id),  -- 前日EOD run_id（NULL可：初回評価など）

  only_base    INTEGER NOT NULL DEFAULT 0 CHECK (only_base IN (0,1)),   -- 前日のみ取引あり。APLの場合、pv_today=0
  only_today   INTEGER NOT NULL DEFAULT 0 CHECK (only_today IN (0,1)),   -- 当日のみ取引あり。APLの場合、pv_base=0

  -- PV（当日/前日）
  pv_today     REAL NOT NULL,                 -- 当日EOD PV（in_today=0 の場合は 0 を入れる運用）
  pv_base      REAL NOT NULL,                 -- 前日EOD PV（in_base=0 の場合は 0 を入れる運用）

  -- PL内訳
  pl_mtm      REAL NOT NULL,                 -- pv_today - pv_base
  cash_flow    REAL NOT NULL DEFAULT 0.0,     -- (base_as_of, as_of] の実現CF合計（受取=+,支払=-） HPLの場合は0
  pl_total    REAL NOT NULL,                 -- pl_mtm + cf_realized

  created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),

  PRIMARY KEY (run_id, trade_id, pl_type)
);

CREATE INDEX IF NOT EXISTS ix_result_eod_run_pltype
  ON result_eod (run_id, pl_type);


-- 感応度の“点”定義（何を何でどう計算するか）
CREATE TABLE IF NOT EXISTS sens_key_def (
  sens_key_id     TEXT PRIMARY KEY,

  measure_id      TEXT NOT NULL REFERENCES measure_def(measure_id), -- DV01/DELTA/Vega/Gamma...
  derivative_order INTEGER NOT NULL CHECK (derivative_order IN (1,2)),
  diff_scheme     TEXT NOT NULL CHECK (diff_scheme IN ('FORWARD','BACKWARD','CENTRAL')),

  -- “どのリスクファクターか”
  rf_target       TEXT NOT NULL CHECK (rf_target IN ('CURVE','FX','VOL','MODEL_PARAM')),
  rf_key          TEXT NOT NULL,       -- curve_id / 'USDJPY' / vol_id / param_name 等
  bucket          TEXT NOT NULL DEFAULT '',  -- テナー/ピラー日/surface点など（UI表示・集計軸）

  shock_method      TEXT NOT NULL CHECK (shock_method IN ('ADD','MUL')),  -- ADD=幅を足す、MUL=率を掛ける
  -- 代表的な bump サイズ（中央差分なら通常 +h と -h が対称）
  h_up            REAL,                -- +のショック。ADDなら値をそのまま足す。MULなら(1+h_up)をかける。
  h_dn            REAL,                -- CENTRALなら通常 -h_up。片側ならNULL可

  description     TEXT,
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX IF NOT EXISTS ix_sens_key_measure_target
  ON sens_key_def (measure_id, rf_target, rf_key, bucket);

CREATE TABLE IF NOT EXISTS result_sensitivity (
  run_id        TEXT NOT NULL REFERENCES run(run_id),
  trade_id      TEXT NOT NULL REFERENCES trade(trade_id),

  -- どの感応度点か
  sens_key_id   TEXT NOT NULL REFERENCES sens_key_def(sens_key_id),

  -- 監査可能性：PVを保持
  pv_base      REAL,   -- V0
  pv_up        REAL,   -- V+
  pv_dn        REAL,   -- V-（CENTRAL/2次で使用。片側ならNULL可）

  -- 方式の明示（ファクト側にも冗長保持：検索/集計をJOIN無しで速くする）
  measure_id      TEXT NOT NULL REFERENCES measure_def(measure_id),
  derivative_order INTEGER NOT NULL CHECK (derivative_order IN (1,2)),
  diff_scheme     TEXT NOT NULL CHECK (diff_scheme IN ('FORWARD','BACKWARD','CENTRAL')),
  rf_target       TEXT NOT NULL CHECK (rf_target IN ('CURVE','FX','VOL','MODEL_PARAM')),
  rf_key          TEXT NOT NULL,
  ccy            TEXT NOT NULL REFERENCES currency(ccy),
  bucket          TEXT NOT NULL DEFAULT '',
  shock_unit      TEXT NOT NULL,
  h_up            REAL NOT NULL,
  h_dn            REAL,

  val            REAL NOT NULL,

  PRIMARY KEY (run_id, trade_id, sens_key_id)
);

-- UI典型: run_id固定 → measure → bucket/rf_key で一覧
CREATE INDEX IF NOT EXISTS ix_result_sens_run_measure_bucket_trade
  ON result_sensitivity (run_id, measure_id, bucket, trade_id);

-- ドリルダウン: run×trade の感応度一覧

-- RF別に集計したい場合
CREATE INDEX IF NOT EXISTS ix_result_sens_run_rf
  ON result_sensitivity (run_id, rf_target, rf_key, bucket);


/* シミュレーション／ストレス結果 */
CREATE TABLE result_simulation (
  run_id        TEXT NOT NULL REFERENCES run(run_id),
  trade_id      TEXT NOT NULL REFERENCES trade(trade_id),

  measure_id       TEXT NOT NULL REFERENCES measure_def(measure_id),  -- 'PV','PL'
  scenario_set_id   TEXT REFERENCES scenario_set(scenario_set_id), -- measure_id=PVの場合はNULL

  ccy           TEXT NOT NULL REFERENCES currency(ccy),
  pv_base       REAL,
  pv_shocked    REAL,
  val           REAL NOT NULL,

  PRIMARY KEY (run_id, trade_id, measure_id, scenario_set_id)
);

-- UI典型(1): run_id → measure(PV/PL) → bucket(''想定) → scenario別に集計（ベース vs ストレス比較）
-- UI典型(2): run_id → measure(PV/PL) → scenario_set_id を選択 → 取引別一覧
CREATE INDEX IF NOT EXISTS ix_result_sim_run_measure_scn_trade
  ON result_simulation (run_id, measure_id, scenario_set_id, trade_id);
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
CREATE TABLE IF NOT EXISTS scenario_set (
  scenario_set_id TEXT PRIMARY KEY,                               -- 例: 'SCN_BASE','SCN_STRESS_2025Q4'
  set_name        TEXT NOT NULL,                                  -- UI表示名
  set_type        TEXT NOT NULL,                                  -- 例: 'STRESS','SENSITIVITY','BACKTEST','REGULATORY'
  description     TEXT,
  owner_user_id   TEXT REFERENCES app_user(user_id) ON DELETE SET NULL,  -- NULL=グローバル
  is_active       INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  created_by      TEXT REFERENCES app_user(user_id) ON DELETE SET NULL,
  updated_at      TEXT,
  updated_by      TEXT REFERENCES app_user(user_id) ON DELETE SET NULL,
  note            TEXT
);

-- UIの典型: 有効なセットを種別/名称で一覧
CREATE INDEX IF NOT EXISTS ix_scenario_set_active_type_name
  ON scenario_set (is_active, set_type, set_name);

CREATE INDEX IF NOT EXISTS ix_scenario_set_owner_active
  ON scenario_set (owner_user_id, is_active);

/* 個別シナリオ定義（UI表示・有効化単位） */
CREATE TABLE IF NOT EXISTS scenario (
  scenario_set_id TEXT    NOT NULL REFERENCES scenario_set(scenario_set_id) ON DELETE CASCADE,
  scenario_id     INTEGER NOT NULL,                                   -- 0,1,2...
  scenario_name   TEXT    NOT NULL,                                   -- UI表示名（例: 'BASE','IR +100bp'）
  description     TEXT,
  is_active       INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
  sort_order      INTEGER NOT NULL DEFAULT 0,
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  created_by      TEXT REFERENCES app_user(user_id) ON DELETE SET NULL,
  updated_at      TEXT,
  updated_by      TEXT REFERENCES app_user(user_id) ON DELETE SET NULL,
  note            TEXT,
  PRIMARY KEY (scenario_set_id, scenario_id),
  UNIQUE (scenario_set_id, scenario_name)
);

-- UIの典型: セット配下のシナリオを有効/表示順で一覧
CREATE INDEX IF NOT EXISTS ix_scenario_list
  ON scenario (scenario_set_id, is_active, sort_order, scenario_id);


/* ショック定義（“個別シナリオ”の子） */
CREATE TABLE IF NOT EXISTS scenario_shock (
  scenario_set_id TEXT    NOT NULL,
  scenario_id     INTEGER NOT NULL,
  target          TEXT    NOT NULL CHECK(target IN ('CURVE','FX','VOL','MODEL_PARAM')),
  target_key      TEXT    NOT NULL,                                  -- 'JPY-OIS','USDJPY','vol_swaption:JPY' 等
  op_tag          TEXT    NOT NULL CHECK(op_tag IN ('ADD_BP','MULT','SET')),
  shock_val       REAL    NOT NULL,
  note            TEXT,
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  created_by      TEXT REFERENCES app_user(user_id) ON DELETE SET NULL,
  updated_at      TEXT,
  updated_by      TEXT REFERENCES app_user(user_id) ON DELETE SET NULL,

  PRIMARY KEY (scenario_set_id, scenario_id, target, target_key, op_tag),
  FOREIGN KEY (scenario_set_id, scenario_id)
    REFERENCES scenario (scenario_set_id, scenario_id)
    ON DELETE CASCADE
);

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
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
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
  enabled        INTEGER NOT NULL DEFAULT 1,                                -- 1=有効,0=無効
  created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  created_by       TEXT,                                                      -- 作成者ID
  updated_at       TEXT,
  updated_by       TEXT
);

CREATE INDEX IF NOT EXISTS ix_eval_preset_active_owner
  ON eval_preset (enabled, owner_user_id);

-- 対象ポートフォリオ（portfolio_scope='PORTFOLIO_LIST' のとき有効）
CREATE TABLE IF NOT EXISTS eval_preset_portfolio (
  preset_id TEXT NOT NULL REFERENCES eval_preset(preset_id) ON DELETE CASCADE,
  portfolio_id TEXT NOT NULL REFERENCES portfolio(portfolio_id),
  PRIMARY KEY (preset_id, portfolio_id)
);

CREATE INDEX IF NOT EXISTS idx_eval_preset_portfolio_portfolio
  ON eval_preset_portfolio(portfolio_id);

-- 計算メジャー（常に 1 件以上存在する想定）
CREATE TABLE IF NOT EXISTS eval_preset_measure (
  preset_id  TEXT NOT NULL REFERENCES eval_preset(preset_id) ON DELETE CASCADE,
  measure_id TEXT NOT NULL REFERENCES measure_def(measure_id),
  PRIMARY KEY (preset_id, measure_id)
);

CREATE INDEX IF NOT EXISTS idx_eval_preset_measure_measure_id
  ON eval_preset_measure(measure_id);

/* =========================
   変更履歴（監査ログ）
   - 設定・マスタ画面の「なぜこの設定になっているか」を残す
   - append-only（更新・削除しない）
   ========================= */
CREATE TABLE IF NOT EXISTS change_log (
  change_id     INTEGER PRIMARY KEY AUTOINCREMENT,
  table_name    TEXT NOT NULL,  -- 例: 'book','scenario_set','eval_preset','market_snapshot' など
  pk            TEXT NOT NULL,  -- 対象PK（単一PKなら値そのまま、複合PKは "k1=v1;k2=v2" 等で直列化）
  action        TEXT NOT NULL,  -- 例: 'INSERT','UPDATE','DISABLE','ENABLE','LOCK','UNLOCK','APPROVE' 等（運用で統一）
  entity_label  TEXT,           -- UI表示名のスナップショット（任意）
  changed_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  changed_by    TEXT,           -- app_user.user_id または 'SYSTEM','BATCH' 等
  comment       TEXT,           -- 変更理由（UIで入力する「メモ」）
  request_id    TEXT,           -- 1操作の相関ID（任意：APIリクエストID等）
  client_tag    TEXT            -- 'UI','CLI','BATCH' 等（任意）
);

-- 典型クエリ：特定マスタ1件の履歴を新しい順に取得（UIの「履歴」タブ想定）
CREATE INDEX IF NOT EXISTS ix_change_log_table_pk_time
  ON change_log (table_name, pk, changed_at);

-- 典型クエリ：全体の最新変更（管理者の監査ビュー想定）
CREATE INDEX IF NOT EXISTS ix_change_log_changed_at
  ON change_log (changed_at);

-- 典型クエリ：ユーザ別の変更追跡（監査・運用）
CREATE INDEX IF NOT EXISTS ix_change_log_changed_by_time
  ON change_log (changed_by, changed_at);

-- 任意：1操作（request_id）で束ねて追えるようにする
CREATE INDEX IF NOT EXISTS ix_change_log_request
  ON change_log (request_id);
