### 詳細な実装の構成
#### src/pyquant/core
- types.py, enums.py: 通貨・指標・営業日ルールなど共通型。
- calendar/（祝日表・営業日調整）、daycount.py、time/（スケジュール生成）。将来の全プロダクトが依存する基盤を集約。
#### src/pyquant/data
- sources/（CSV/DB/API アダプタ）、models/（Pydantic/SQLAlchemy）、transformers/。マーケットデータ・取引データの取り込みと検証を一箇所で管理。
#### src/pyquant/market
- curves/（例：interpolation/, discount.py, forward.py, bootstrap.py）、vol/（volatility surface）、fx/（為替レート）。キャリブレーションや更新ロジックもここに集める。
#### src/pyquant/instruments
- rates/（IRS, Bond, 金利先物, FRA, Cap/Floor など）、fx/（FX Forward/Option）、将来の credit/, equity/。各取引のドメインモデルとleg生成、キャッシュフロー計算をまとめる。
#### src/pyquant/analytics
- pv/（プライシングエンジン：例 pv/irs.py）、risk/（Greek, DV01, Vega）、cva/, simulation/（MC, シナリオ）、common/（数値計算法、線形代数、ルート検索など）。高速化のための NumPy/SciPy 実装やJITをここで集中管理する。
#### src/pyquant/portfolio
- ミドル／バック業務を意識したバッチ評価やレポーティングの入り口。
#### src/pyquant/workflows
- ジョブ定義、CLI/cron 連携、パイプライン制御。EODやシミュレーションのオーケストレーションを担う。
#### src/pyquant/utils
- logging、設定ロード、プロファイリング、キャッシュ、共通helper。
#### scripts/
- データ初期化、デモ評価、メンテナンススクリプト。ライブラリ本体とは分離。
#### tests/
- 実装に合わせたミラーツリー構成。pytest で単体・統合・回帰を整理。
#### doc
- 各種設計書や仕様書などのドキュメント。
#### notebooks/
- キャリブレーション検証やリサーチ用。コードとは分けて管理。