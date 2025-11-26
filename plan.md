## 実装したいもの
- アプリの名前はpyquant
- 最終的には、デリバティブの取引管理、価格計算、感応度計算、リスク計測、その他銀行業務で使われる計数計算ができるシステムを実装したい
- ただし、勉強のために個人で実装する簡易的なものである
- 簡易的といっても、基本的な機能は揃え、小さな銀行であれば業務に使えるかもしれないと思えるような本格的なものにしたい
- SLAは厳密には定めず、機能の実装をすることが優先だがその中でなるべく速い処理を目指す
- 処理速度の基準は設けないが、最終的にはなるべく速い処理にできるようにしたいため、適切な状況においてはCUDA、Cupy、Numbaなどの高速化のためのライブラリ・仕組みを使うための拡張を意識したコードにする

## 環境
- OS : Windows11 WSL2 (Ubuntu)
- Python : 3.12.11
- DB : SQLite
- CPU : Intel Core i7
- GPU : Nvidia GeForce RTX5060

### 実装方針
- まずは取引管理とPV計算の実装を行い、感応度、CVA、リスク計測や、その他機能はその後に実装を追加していくこととする。そのため、それらの拡張を意識して実装を進めていく。

## 設計(第一弾の実装)
### 基本的な仕様(すべて拡張する前提)
#### プロダクト
- IRS(固定vs変動)
- 債券(固定債、変動債、割引債)
- 金利先物
- FRA
- FXフォワード
- ヨーロピアンFXオプション
- Cap/Floor
- ヨーロピアンSwaption
#### 測定値
- PV, PL
#### 通貨
- JPY
- USD
- EUR
#### 単位
- 通貨は取引に準ずる
- レートは実数
#### 時点
- 評価日tは変更可能とする(ただし、取引内容なレートの情報は評価日の変更に影響しない)
- タイムゾーンはAsia/Tokyo
#### 日数計算
- ACT/ACTやACT/360などの日数計算規則はイールドカーブごとに設定可能とする
- Modified Followingなどの営業日規則は取引ごとに設定可能とする
- 休日は土日以外も考慮する
#### イールドカーブ
- OIS
- そのほかのカーブ(OIS以外のカーブも扱える)
#### 精度
- float64
#### モデル
市場で価格がクォートされている場合はその価格に合わせるようにキャリブレーションする。
- IRS(固定vs変動) : DCF法
- 債券(固定債、変動債、割引債) : DCF法
- 金利先物：DCF法
- FRA：DCF法
- FXフォワード : カバード金利平価
- ヨーロピアンFXオプション : Garman–Kohlhagen（Black, lognormal）
- Cap/Floor : Black caplet(Shift付)
- ヨーロピアンSwaption : Black swaption(Shift付)

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
