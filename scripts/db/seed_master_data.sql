PRAGMA foreign_keys = ON;

BEGIN;

/* =========================================================
   0) 列挙マスタ（基本は参照専用：v1ではスクリプトで投入）
   ========================================================= */

-- 0.1 calendar role
INSERT OR IGNORE INTO m_calendar_role (role, description) VALUES
  ('DEFAULT',    '通貨の一般業務用のデフォルトカレンダー（スケジュール生成等）'),
  ('SETTLEMENT', '決済用カレンダー（FX/決済日計算などで将来拡張）'),
  ('FIXING',     'Fixing 用カレンダー（参照金利の Fixing 日判定等）');

-- 0.2 interpolation method (curve)
INSERT OR IGNORE INTO m_interp_method (interp_method, description) VALUES
  ('LOG_LINEAR_DF',        'DF を対数線形補間'),
  ('LINEAR_ZERO',          'ゼロレートを線形補間'),
  ('CUBIC_SPLINE_ZERO',    'ゼロレートを3次スプライン補間'),
  ('MONOTONE_CONVEX_SPLINE_ZERO',    'ゼロレートをmonotone convex spline補間');

-- 0.3 extrapolation method (UI用の列挙マスタ。curve_def は CHECK で制約)
INSERT OR IGNORE INTO m_extrap_method (extrap_method, description) VALUES
  ('FLAT_FWD',    '右端：フォワードフラット（標準）'),
  ('FLAT_ZERO',   '右端：ゼロフラット'),
  ('LINEAR_ZERO', '右端：ゼロ線形外挿');

-- 0.4 trade product
INSERT OR IGNORE INTO m_trade_product (product, description) VALUES
  ('IRS',       'Interest Rate Swap'),
  ('FRA',       'Forward Rate Agreement'),
  ('IRFUT',     'Interest Rate Futures'),
  ('BOND',      'Bond / Cash security'),
  ('FXFWD',     'FX Forward'),
  ('FXOPT',     'FX Option'),
  ('CAPFLOOR',  'Cap/Floor'),
  ('SWAPTION',  'Swaption');

-- 0.5 daycount
INSERT OR IGNORE INTO daycount (code, display_name, formula_tag, notes) VALUES
  ('ACT/360',      'Actual/360',          'ACT_360',        NULL),
  ('ACT/365F',     'Actual/365 Fixed',    'ACT_365F',       NULL),
  ('ACT/ACT-ISDA', 'Actual/Actual (ISDA)','ACT_ACT_ISDA',   NULL),
  ('30E/360',      '30E/360',             'THIRTY_E_360',   '30/360 European');

-- 0.6 business day convention
INSERT OR IGNORE INTO bizday_convention (code, display_name, rule_tag, nearest_tiebreaker, notes) VALUES
  ('F',    'Following',          'FOLLOWING',      NULL,   NULL),
  ('MF',   'Modified Following', 'MOD_FOLLOWING',  NULL,   NULL),
  ('P',    'Preceding',          'PRECEDING',      NULL,   NULL),
  ('MP',   'Modified Preceding', 'MOD_PRECEDING',  NULL,   NULL),
  ('N',    'Nearest',            'NEAREST',        'NEXT', '休日の前後が同距離の場合は翌営業日優先'),
  ('NONE', 'None',               'NONE',           NULL,   '休日調整を行わない');

-- 0.7 calendar (holiday set)
INSERT OR IGNORE INTO calendar_def (cal_id, display_name, time_zone) VALUES
  ('JPTO', 'Japan (Tokyo)',        'Asia/Tokyo'),
  ('USNY', 'US (New York)',        'America/New_York'),
  ('EUTA', 'Euro Area (Brussels)', 'Europe/Brussels'),
  ('CHZU', 'Switzerland (Zurich)', 'Europe/Zurich');

-- NOTE:
-- market_holiday は国・市場ごとに頻繁に変わるため、本シードでは投入しません。
-- 必要に応じて、運用で扱う期間の休日を別スクリプトで追加してください。

/* =========================================================
   1) 通貨マスタ
   ========================================================= */

INSERT OR IGNORE INTO currency (ccy, name, iso_numeric, minor_unit, symbol, spot_lag, enabled, valid_from, retired_at)
VALUES
  ('JPY', 'Japanese Yen',   392, 0, '¥',  2, 1, '1970-01-01', NULL),
  ('USD', 'US Dollar',      840, 2, '$',  2, 1, '1970-01-01', NULL),
  ('EUR', 'Euro',           978, 2, '€',  2, 1, '1999-01-01', NULL);

-- 1.2 currency -> default calendar mapping
INSERT OR IGNORE INTO currency_calendar (ccy, role, cal_id, enabled)
VALUES
  ('JPY', 'DEFAULT', 'JPTO', 1),
  ('USD', 'DEFAULT', 'USNY', 1),
  ('EUR', 'DEFAULT', 'EUTA', 1);

/* =========================================================
   2) 参照金利（Fixing/Index）マスタ
   ========================================================= */

INSERT OR IGNORE INTO ref_rate_rule (
  index_id, ccy, tenor, daycount, display_name, index_family, rate_type,
  fixing_cal_id, fixing_bdc, fixing_time_local, fixing_tz, publication_lag_days,
  accrual_conv, lookback_days, lockout_days, source_tag, enabled, valid_from, retired_at
) VALUES
  ('JPY-TONAR',     'JPY','ON', 'ACT/365F','TONAR (O/N)',          'TONAR','ON',
   'JPTO','F', '17:00','Asia/Tokyo',        0, 'COMPOUND_IN_ARREARS', 0,0, 'MANUAL', 1, '1970-01-01', NULL),

  ('USD-SOFR',      'USD','ON', 'ACT/360', 'SOFR (O/N)',           'SOFR','ON',
   'USNY','F', '08:00','America/New_York', 1, 'COMPOUND_IN_ARREARS', 0,0, 'MANUAL', 1, '2018-04-03', NULL),

  ('USD-SOFR-3M',   'USD','3M', 'ACT/360', 'SOFR (Term 3M)',       'SOFR','TERM',
   'USNY','F', NULL, 'America/New_York',   0, 'TERM_QUOTE',         0,0, 'MANUAL', 1, '1970-01-01', NULL),

  ('EUR-ESTR',      'EUR','ON', 'ACT/360', '€STR (O/N)',           'ESTR','ON',
   'EUTA','F', '08:00','Europe/Brussels',  1, 'COMPOUND_IN_ARREARS', 0,0, 'MANUAL', 1, '2019-10-02', NULL);

/* =========================================================
   3) カーブ定義（Pricing 側で参照する “論理カーブ”）
   ========================================================= */

INSERT OR IGNORE INTO pricing_curve_def (
  curve_id, ccy, curve_type, ref_rate_id,
  daycount, compounding,
  interp_method, extrap_left, extrap_right, pillar_mode,
  build_method, cal_id, collateral_ccy,
  valid_from, valid_to, description
) VALUES
  ('JPY-OIS',       'JPY','OIS',     NULL,
   'ACT/365F','CONT',
   'LOG_LINEAR_DF','FLAT_FWD','FLAT_FWD','TENOR',
   'BOOTSTRAP','JPTO','JPY',
   '1970-01-01', NULL, 'JPY OIS discount/projection (prototype)'),

  ('USD-OIS',       'USD','OIS',     NULL,
   'ACT/360','CONT',
   'LOG_LINEAR_DF','FLAT_FWD','FLAT_FWD','TENOR',
   'BOOTSTRAP','USNY','USD',
   '1970-01-01', NULL, 'USD OIS discount (SOFR-OIS)'),

  ('USD-SOFR-3M',   'USD','FORECAST','USD-SOFR-3M',
   'ACT/360','CONT',
   'LOG_LINEAR_DF','FLAT_FWD','FLAT_FWD','TENOR',
   'BOOTSTRAP','USNY','USD',
   '1970-01-01', NULL, 'USD 3M forecast curve (SOFR term)'),

  ('EUR-OIS',       'EUR','OIS',     NULL,
   'ACT/360','CONT',
   'LOG_LINEAR_DF','FLAT_FWD','FLAT_FWD','TENOR',
   'BOOTSTRAP','EUTA','EUR',
   '1970-01-01', NULL, 'EUR OIS discount (ESTR-OIS)');

/* =========================================================
   4) 評価プロファイル（「どのカーブ/ボラを参照するか」のルール）
   ========================================================= */

INSERT OR IGNORE INTO pricing_profile (profile_id, profile_name, description, enabled)
VALUES
  ('STD_OIS', '標準（OIS割引）', 'v1: 通貨ごとにOIS系カーブを参照する標準プロファイル', 1);

-- v1: まずは IRS に対してのみ最小セットを用意（他商品は実装が固まった段階で追加）
INSERT OR IGNORE INTO pricing_profile_map (profile_id, product, ccy, md_role, md_id, priority, note) VALUES
  ('STD_OIS','IRS','JPY','DISCOUNT_CURVE',  'JPY-OIS',       10, NULL),
  ('STD_OIS','IRS','JPY','FORECAST_CURVE',  'JPY-OIS',       10, NULL),

  ('STD_OIS','IRS','USD','DISCOUNT_CURVE',  'USD-OIS',       10, NULL),
  ('STD_OIS','IRS','USD','FORECAST_CURVE',  'USD-SOFR-3M',   10, NULL),

  ('STD_OIS','IRS','EUR','DISCOUNT_CURVE',  'EUR-OIS',       10, NULL),
  ('STD_OIS','IRS','EUR','FORECAST_CURVE',  'EUR-OIS',       10, NULL);

/* =========================================================
   5) マーケットデータベンダ（取込の由来管理）
   ========================================================= */

INSERT OR IGNORE INTO md_vendor (vendor_id, display_name, enabled, note) VALUES
  ('MANUAL', 'Manual / Test Data', 1, '手入力・テストデータ'),
  ('BBG',    'Bloomberg',          1, NULL),
  ('RTRS',   'Refinitiv',          1, NULL);

/* =========================================================
   6) ポートフォリオ
   ========================================================= */

INSERT OR IGNORE INTO portfolio (portfolio_id, description, owner, parent_portfolio_id, portfolio_type, is_active)
VALUES
  ('DEMO', 'デモポートフォリオ（初期）', 'SYSTEM', NULL, 'ADHOC', 1);

/* =========================================================
   7) ユーザ・ロール（最低限）
   ========================================================= */

INSERT OR IGNORE INTO role (role_id, role_name, description, is_system) VALUES
  ('ADMIN',  'Administrator', '全機能の管理・実行', 1),
  ('USER',   'User',          '通常ユーザ（取引/評価/参照）', 1),
  ('VIEWER', 'Viewer',        '参照専用', 1);

-- NOTE:
-- password_hash / salt は認証実装方式に依存するため、本シードではプレースホルダを入れます。
-- 認証実装後に、適切なハッシュ値に更新してください。
INSERT OR IGNORE INTO app_user (user_id, display_name, password_hash, salt, enabled, note)
VALUES
  ('admin', 'Admin', 'CHANGE_ME', 'CHANGE_ME', 1, '初期管理者（要パスワード更新）');

INSERT OR IGNORE INTO user_role (user_id, role_id, granted_by)
VALUES
  ('admin', 'ADMIN', 'SYSTEM');

/* =========================================================
   8) メジャー定義（結果の表示・保存のための共通辞書）
   ========================================================= */

INSERT OR IGNORE INTO measure_def (measure_id, measure_name, category, unit, default_calc_method, preferred_store, enabled, description)
VALUES
  ('PV',        'Present Value',  'VALUATION',   'CCY',  'FULL_REVAL', 'CORE', 1, '現在価値'),
  ('DV01',      'DV01',           'SENSITIVITY', 'BP',   'BUMP_REVAL', 'EXT',  1, '金利1bp感応度（曲線）'),
  ('FX_DELTA',  'FX Delta',       'SENSITIVITY', 'CCY',  'BUMP_REVAL', 'EXT',  1, 'FXレートの感応度'),
  ('VEGA',      'Vega',           'SENSITIVITY', 'CCY',  'BUMP_REVAL', 'EXT',  1, 'ボラティリティ感応度'),
  ('GAMMA',     'Gamma',          'SENSITIVITY', 'CCY',  'BUMP_REVAL', 'EXT',  1, '2階感応度（将来拡張）');

-- 代表的な感応度キー（v1の最小セット）
INSERT OR IGNORE INTO sens_key_def (
  sens_key_id, measure_id, derivative_order, diff_scheme,
  rf_target, rf_key, bucket, shock_method, h_up, h_dn, description
) VALUES
  ('DV01_JPY_OIS_ALL', 'DV01', 1, 'CENTRAL', 'CURVE', 'JPY-OIS', '', 'ADD',  0.0001, -0.0001, 'JPY-OIS parallel DV01'),
  ('DV01_USD_OIS_ALL', 'DV01', 1, 'CENTRAL', 'CURVE', 'USD-OIS', '', 'ADD',  0.0001, -0.0001, 'USD-OIS parallel DV01'),
  ('FXD_USDJPY_1PCT',  'FX_DELTA', 1, 'CENTRAL', 'FX', 'USDJPY', '', 'MUL', 0.01,  -0.01,  'USDJPY 1% bump FX delta');

/* =========================================================
   9) シナリオ（ストレス等）
   ========================================================= */

INSERT OR IGNORE INTO scenario_set (scenario_set_id, set_name, set_type, description, owner_user_id, is_active, note)
VALUES
  ('SCN_BASE', 'Base/Stress (Initial)', 'STRESS', '初期シナリオ集合（ベース＋簡易ストレス）', NULL, 1, NULL);

INSERT OR IGNORE INTO scenario (scenario_set_id, scenario_id, scenario_name, description, is_active, sort_order)
VALUES
  ('SCN_BASE', 0, 'BASE',      'No shock',                         1, 0),
  ('SCN_BASE', 1, 'IR+100bp',  'Parallel +100bp to key curves',    1, 10),
  ('SCN_BASE', 2, 'FX+10%',    'USDJPY x 1.10',                    1, 20);

-- scenario shocks（ベースは shock なし）
INSERT OR IGNORE INTO scenario_shock (scenario_set_id, scenario_id, target, target_key, op_tag, shock_val, note)
VALUES
  ('SCN_BASE', 1, 'CURVE', 'JPY-OIS', 'ADD_BP', 100.0, NULL),
  ('SCN_BASE', 1, 'CURVE', 'USD-OIS', 'ADD_BP', 100.0, NULL),
  ('SCN_BASE', 2, 'FX',    'USDJPY',  'MULT',   1.10,  NULL);

/* =========================================================
   10) 評価条件プリセット（UIから編集対象）
   ========================================================= */

INSERT OR IGNORE INTO eval_preset (
  preset_id, preset_name, description, owner_user_id,
  as_of_policy, as_of_fixed,
  snapshot_policy, snapshot_id_fixed,
  scenario_set_id,
  portfolio_scope, ccy_scope,
  enabled, created_by
) VALUES
  ('PRESET_STD', '標準評価（初期）',
   'v1: TODAY / LATEST_LOCKED / SCN_BASE / DEMO / PV+DV01',
   NULL,
   'TODAY', NULL,
   'LATEST_LOCKED', NULL,
   'SCN_BASE',
   'PORTFOLIO_LIST', 'ALL',
   1, 'SYSTEM');

INSERT OR IGNORE INTO eval_preset_portfolio (preset_id, portfolio_id)
VALUES
  ('PRESET_STD', 'DEMO');

INSERT OR IGNORE INTO eval_preset_measure (preset_id, measure_id)
VALUES
  ('PRESET_STD', 'PV'),
  ('PRESET_STD', 'DV01');

COMMIT;
