# SaaS_A 月額課金レポート アプリ設計書（saas-a-billing）

作成日: 2026-08-27
ステータス: レビュー済み（2026-08-28 決定事項反映）・実装着手
親設計書: [../../DESIGN.md](../../DESIGN.md)（example-backoffice プラットフォーム）
注記: 図は `images/` の生成画像を参照する（AWS 構成図は Python diagrams による PNG、状態遷移図は mermaid-cli による SVG。生成スクリプトは同ディレクトリ）。

---

## 1. 目的

SaaS_A 本番環境の全テナント・全地点を毎月の締め日で棚卸しし、ベンダーAから届く月額ライセンス請求書を照合するための一覧（Excel）を自動生成・自動配布する。

- 対象: ベンダーA → 弊社の請求（顧客への請求ではない）
- 現状: `.claude/skills/saas-a-billing-report`（AI が検証環境を手動集計して PDF 化）。これを本番環境・無人運転に置き換える
- 運用開始目標: 2026年10月分（10/20 締め → 10/21 06:00 初回自動実行）

## 2. 業務ルール

### 2.1 締め日時と課金対象期間
- 締め日時は設定できる。設定項目は `cutoff_day`（1〜28、既定 20）と `cutoff_time`（`HH:MM`、既定 `24:00`。`24:00` は「その日の終わりまで含む」= 翌日 00:00 の意味）
- 締め日時 = 対象月の `cutoff_day` 日 `cutoff_time`
- 課金対象期間 = [前月の締め日時, 当月の締め日時)。既定では **前月21日 00:00:00 〜 当月20日 23:59:59**（受信日時 `change_datetime` 基準、JST）。本書では課金対象期間を「締め期間」とも呼ぶ
- 対象月 = 締め日が属する年月（既定では 20 日が属する月）
- 商慣習上の「20日締め」は 20 日いっぱいを含む解釈であり、ベンダーA営業の「20 日に 1 か月分を締める（人力）」もこの解釈で運用する
- 設定画面の表記は「締め日: 毎月 20 日 / 締め時刻: 24:00（その日の終わりまで含む）」とし、レポート先頭に課金対象期間を展開して表記する

### 2.2 課金判定（地点ごと）
1. **除外（課金対象外）**: テナントの説明欄（`tenant_description`）または地点の備考欄（`device_description`）に除外キーワード（既定「デモ」）を含む地点。地点名・地点CD・現場名は判定に使わない
2. **通信なし（0円）**: 除外でない地点のうち、課金対象期間内に受信データが 1 件も無い地点（課金対象期間の export に当該地点CDの行が 1 行も現れない地点）。単価は表示するが金額 0 円、色分けで明示する
3. **課金**: 上記以外。単価 = 単価表（保存期間 × 項目数帯 / カメラ）

優先順位: 除外 > 通信なし > 課金。

- 課金対象期間内の受信が「期間の初日のみ」または「最終日のみ」の地点は、判定・金額を変えずに明細の備考へ「境界日のみ受信」と印を付ける（赤字ではなく通常色）。人力締めとの境界ずれを説明するため

### 2.3 単価表（既定値: support-tracker #2001、2025/9/1 時点）

| 項目数帯 | 3年保存 | 1年保存 |
|---|---|---|
| 〜500 | 650円 | 450円 |
| 〜800 | 1,000円 | 700円 |
| 〜1,600 | 1,200円 | 900円 |
| 〜1,700 | 1,350円 | 1,000円 |
| カメラ地点 | 1,800円 | 1,400円 |

- 保存期間はテナント属性（`retention_period` = 1 または 3）
- 項目数は地点のフォーマット項目数（`device/list` 応答の `format_detail` 配列の要素数。MCP 応答では `format_detail_count`）
- 1,700 超は上限帯の単価を仮適用して課金し（合計に含める）、赤字の「要確認」を付ける。`retention_period` が 1 / 3 以外（null 含む）の地点も 3 年扱いの仮単価で課金し、同じく「要確認」を付ける
- 項目数が 0 の地点は 0 円で計上し、赤字の「要確認」（`NO_ITEMS`）を付ける
- 単価表は設定画面から編集でき、**適用開始日付きで履歴保持**する。各月の集計は「その締め日に有効な単価表」で計算し、レポートに使用した単価表を明記する
- **既定値の自動シード**: 単価表が 1 版も登録されていない場合、初回の読み出し時に本表（#2001）を適用開始日 `2025-09-01` で自動投入する（未来日の版だけがある状態では投入しない。登録済みの版には触れない）。手作業での初版登録は不要
- 設定画面の単価表 UI: 「現在の単価」を円表記の表で常時表示し、改定は「単価を改定する」ボタンで開く入力フォームから登録する（適用予定の未来版は予告表示、過去の版は折りたたみの履歴に出す）

### 2.4 カメラ地点の判定
- 課金対象期間の export（CSV）はフォーマットごとのブロックに分かれ、ブロック先頭にヘッダ行が再出現する。ヘッダ行に「Base64形式データ」列を含むブロックに現れた地点CDをカメラ地点とみなす（R4 実測: CamA-MC のヘッダは `field_name,point_cd,point_name,change_datetime,パケット種別,ファイル名,Base64形式データ`。Base64 列の値は "SUCCESS" 固定で画像本体は含まれない）
- 課金対象期間に受信が無いカメラ地点はヘッダで判定できないため、設定画面の「カメラ扱いフォーマット名リスト」（`format_name` の完全一致）で補強する。両方の判定結果は OR で扱い、明細にどちらで判定したかを残す

### 2.5 照合時に差異が出うる要因（レポート脚注に記載）
- 課金対象期間中に削除された地点は API 一覧に現れない。前回 RUN の結果と突き合わせて「消えた地点」として検出し、シート2に列挙する（金額は計上しない）
- 課金対象期間中に追加された地点は、受信があれば 1 か月分として課金扱いになる（日割り無し）
- ベンダーA側の締め処理時刻との微差（境界日のみ受信の地点は明細に印を付ける）

## 3. 機能要件

### 3.1 月次自動実行
- EventBridge Scheduler が毎日 06:00 JST に trigger Lambda を起動する。trigger は「最新の締め日時が現在時刻以前」かつ「その対象月の RUN が `succeeded`（`distributed` を含む）でない」場合にステートマシンの実行を開始する。既定（20日 24:00 締め）では毎月 21 日 06:00 に実行される
- 収集 → 判定・集計 → Excel 生成 → S3 保存 → 共通 notify へ投函（メール: Excel を添付、Slack: テキスト + サマリ + ポータル URL）→ 実行履歴に記録
- RUN が `failed` の場合も翌日 06:00 に自動再実行する（1 日 1 回、最大 `auto_retry_days` 回。既定 5）。上限に達したら「要手動」のアラーム通知を出し、自動再実行を止める
- 「未実行の検知」はこの trigger のロジックで兼ねる（別アラームは作らない）
- 同一対象月の二重送信を防ぐ（実行履歴テーブルで冪等化。再実行は明示操作のみ）

### 3.2 手動実行
- 画面から「対象月を指定して実行」できる
- **集計対象環境は選べない**。サイトに固定された環境（3.7）で必ず実行する。画面は固定環境をチップで表示するだけで、`POST /runs` にも環境を送らない
- 用途: テスト実行（`distribute=false` で配布せず画面と Excel だけ確認する）、失敗後の再実行、再送（`force=true` で成功済みの月を再集計して配布し直す）
- **配布の既定は「しない」**（`distribute=false`）。配布する場合は画面で実際の宛先（メール宛先・Slack チャンネル）を表示し、確認チェックを必須にする
- 固定環境の SSM 3 件（base-url / user-id / password）が未登録の場合は実行前にバリデーションエラーにする
- 誰がいつ手動実行したかを RUN に残す（`request_history`。直近 20 件。7.1）

### 3.3 設定画面
| 設定 | 内容 | 既定値 |
|---|---|---|
| 集計対象環境 | `target_env`。**表示のみ**（3.7 のスタックパラメータで固定。画面からは変更できない） | サイトの固定値 |
| 締め日 | `cutoff_day`。1〜28 | 20 |
| 締め時刻 | `cutoff_time`。`HH:MM`。`24:00` は「その日の終わりまで含む」 | `24:00` |
| 自動再実行上限日数 | `auto_retry_days`。**スケジュール起動による**自動再実行の回数上限（1 日 1 回）。手動実行（`force` を含む）はこの枠を消費しない | 5 |
| 自動実行 | 有効 / 無効（無効時は trigger を空振りさせる。dev は無効運用） | 無効（導入手順で有効化） |
| 自動実行の開始月 | `auto_run_from_month`。`YYYY-MM`。これより前の対象月は自動実行しない | 導入月（seed 時は当月） |
| 実行時刻 | 06:00 JST 固定（Scheduler の cron。変更は template の修正） | 06:00 |
| メール配布 | `mail_enabled`。メール配布の有効 / 無効（Slack とは独立のトグル） | 無効 |
| Slack 配布 | `slack_enabled`。Slack 配布の有効 / 無効（メールとは独立のトグル） | 無効 |
| メール宛先 | To / Cc のアドレスリスト。**メール配布が有効なときのみ To が必須** | To=`["devA@example.co.jp"]` / Cc=空（dev / prod とも） |
| Slack チャンネル ID | `slack_channel_id`。**Slack 配布が有効なときのみ必須**。Slack のチャンネル ID（例 `C0123ABCD`）。名前は改称で壊れるため ID で保持する。送信は共通 notify 経由（トークンは core が保持） | `#proj-saas-a-billing` のチャンネル ID（初期セットアップで登録） |
| Slack チャンネル表示名 | `slack_channel_label`（任意）。画面表示用の人向けメモ。投稿先の決定には使わない | `#proj-saas-a-billing` |
| Slack メンション | 本文先頭に付ける文字列（`<!here>` / `<@U0123>` / `<!subteam^S0123>`）。空可 | 空 |
| Slack 文面 | テンプレート。プレースホルダ `{month}` `{period}` `{tenants}` `{devices}` `{billable}` `{total}` `{url}` | 3.6 の既定文 |
| 単価表 | 保存期間 × 項目数帯、カメラ単価、適用開始日 | #2001 の値 |
| 除外キーワード | 説明欄・備考欄に含まれたら除外（配列） | `["デモ"]` |
| カメラ扱いフォーマット名 | 明示指定リスト | 空 |
| 接続先 | 固定環境に対応する SSM SecureString のベース URL。画面は表示のみ（ホスト名と環境バッジ） | dev=検証環境、prod=本番 |

- 変更は誰がいつ何を変えたかを記録する（`updated_by` = Cognito の email、`updated_at` = 更新日時）
- **配布は既定で両方無効**。メール / Slack を配布に使うには設定画面で明示的に有効化する（既存の設定レコードに `mail_enabled` / `slack_enabled` が無い場合は読み出し時に無効として補完する）。両方無効でも集計・画面の内訳表示・Excel ダウンロードは全く制限されない
- Slack が Webhook フォールバック（プラットフォーム設計 8.5）で動作している間は、チャンネル ID の設定が無視される旨を画面に表示する
- **`target_env` は保存できない**。`PUT /settings` に固定値と異なる値が来たら 400 `TARGET_ENV_FIXED` で拒否する（省略は許容し、画面は送らない）。固定環境の SSM SecureString 3 件（`base-url` / `user-id` / `password`）が未登録なら 400 `TARGET_ENV_NOT_CONFIGURED` で保存を拒否する
- 画面上部のサマリカードに固定の集計対象環境と接続先ホストを常時表示し、検証環境のときはオレンジ、本番のときは緑のバッジを出す。「このサイトでは固定です（画面からは変更できません）」の注記を添える
- **保存できないときは理由を画面に表示する**（入力エラーの列挙、SSM 未登録の名指し等）
- 未保存の変更があるまま画面を離れようとしたら確認ダイアログで止める（`onBeforeRouteLeave` + `beforeunload`）。「変更を破棄」でサーバの値に戻す
- 保存結果・単価改定の結果はスナックバーで通知する。`admin` 以外は入力を `disabled` にして読み取り専用であることを見た目で分かるようにする

### 3.4 レポート閲覧
- 過去の実行結果一覧（集計対象環境、対象月、実行日時、テナント数、地点数、通信なし数、除外数、合計金額、配布状況、Excel ダウンロード）
- **環境の絞り込みは無い**（サイト固定のため）。ただし各行の環境バッジ（検証=オレンジ、本番=緑）は残す。dev には過去の `RUN#production` 行が残るため識別に要る
- 一覧は画面に入るたび取り直し、更新ボタンと最終更新時刻を出す。実行中（`queued` / `running`）の RUN がある間だけ 10 秒ごとに自動更新し、画面を離れたら止める
- 選択した月の内訳を画面表示（テナント → 現場 → 地点。色分けは Excel と同じ）。**合計金額は内訳の先頭に大きく太字で出す**（一覧の合計金額列も太字）
- 判定の凡例（判定 5 種 + 要確認 3 種）を折りたたみで添える
- 日時は JST `YYYY-MM-DD HH:mm`（課金対象期間のみ秒まで）、金額は `¥1,234,567` に統一する

### 3.5 Excel レイアウト
2 シート構成とする。両シートの先頭に、設定値から生成した処理仕様ブロック（対象月・**合計金額**・集計処理日時・締め日時・集計対象環境・課金対象期間・除外ルール・使用単価表・環境・試行回数・取得失敗テナント）を置く。合計金額は太字・大きめ・塗りで強調し、シートを開いた瞬間に読めるようにする。

- シート1「請求照合」: ベンダーA請求書との突合用。判定が「課金」の地点だけを載せる。合計ブロック（課金地点数・合計金額・保存期間別内訳・カメラ内訳）→ テナントブロック（見出し・地点行・小計）→ 課金地点なしのテナント一覧 → 使用単価表 → 脚注
- シート2「全地点（管理用）」: 判定別件数 → テナント別サマリ表 → 全地点明細（Excel のテーブル + オートフィルタ）
- 色: 通信なし=アンバー、除外=青、取得失敗=灰、消えた地点=灰字、要確認=赤文字。保存期間セルは 3年=緑、1年=紫、不明=赤字（モノクロ印刷でも文字で判別できる）
- 日本語フォント: Meiryo（中華フォント禁止）

レイアウトの詳細は 6.6 に記載する。

### 3.6 通知
- **配布先はメール（`mail_enabled`）と Slack（`slack_enabled`）の独立トグルで決まる**（既定は両方無効）。有効な配布先が無ければ何も送らない（集計・画面表示・ダウンロードは影響を受けない）
- メール: 件名「【SaaS_A】月額ライセンス料金一覧 YYYY年M月分（M/20締め）」（月はゼロ埋めしない）、本文にサマリ（テナント数・地点数・通信なし・除外・要確認・消えた地点・合計）と画面URL、Excel 添付
- Slack: テキスト + サマリ + ポータル URL。既定文面は「{month} の SaaS_A 月額課金レポートを送付しました。ベンダーA請求書との照合を忘れずに。合計 {total}（課金 {billable} 地点）」＋ポータル URL。Excel は添付しない
- 集計対象環境が検証環境のときは、件名末尾に `[検証環境]`、Slack 本文の先頭に `【検証環境】` を付け、添付名を `saas_a_billing_{YYYY-MM}_verification.xlsx` にする（6.8）
- 失敗時・自動再実行の上限到達時: 設定で有効な配布先（Slack チャンネル / `mail_to`）にアラーム通知（エラー概要・再実行手順の画面URL）。別チャンネルは作らない
- **手動実行の「配布しない」（`distribute=false`）は設定より優先する**。この RUN では成功レポートも失敗通知も一切送らない（テスト実行で通知が飛ばないことを保証する）
- 宛先は共通 notify が送信直前に `@example.co.jp` だけに絞る（プラットフォーム設計 8.2）。設定画面のバリデーションを抜けた社外アドレスもここで止まる

### 3.7 集計対象環境の固定

集計対象の SaaS_A 環境（`target_env`）は**サイトごとにスタックパラメータで固定**し、画面・API・実行入力のいずれからも切り替えられない。

| サイト | `TargetEnv` | 集計対象 | ラベル |
|---|---|---|---|
| dev（`backoffice-dev.example-cloud.com`） | `verification` | SaaS_A 検証環境 | 「検証環境」オレンジ |
| prod（`backoffice.example-cloud.com`） | `production` | SaaS_A 本番 | 「本番」緑 |

- `TargetEnv` は `apps/saas-a-billing/template.yaml` のパラメータ（`AllowedValues: [verification, production]`）で、`samconfig.toml` が dev=`verification` / prod=`production` を渡す。全 Lambda に環境変数 `TARGET_ENV` として入る
- **正は環境変数**。`trigger` / `init` / `aggregate` / `mark_failed` は設定レコードや実行入力ではなく `TARGET_ENV` を読む。`GET /settings` の `target_env` は保存値に関わらず固定値を返す
- `TARGET_ENV` が未設定・想定外の値のときは `verification` にフォールバックする（誤って本番ラベルで配布するより検証ラベルで止まるほうが安全。prod では `targets/verification/*` が SSM に無いので `Init` が `TARGET_ENV_NOT_CONFIGURED` で落ちる）。フォールバックが起きたことは警告ログに残す
- **参照する SSM は固定環境の 3 本だけ**。IAM の `ssm:GetParameter*` も `targets/{TargetEnv}` とその配下に限定してあるため、もう一方の環境は読めない
- **ラベルは維持する**（環境バッジ、メール件名の `[検証環境]`、Slack 本文先頭の `【検証環境】`、Excel ファイル名の `_verification`、S3 プレフィックス、RUN の PK `RUN#{target_env}`）。成果物だけを見ても検証か本番かを判別できる状態を保つ

この方式（サイト固定）を採った理由: 画面から本番を選べる限り、検証のつもりで本番を集計して社外向けの請求照合データを配布してしまう事故が起こりうる。環境の選択をデプロイ時の決定に落とすことで、実行時の操作ミスで本番に触れる経路そのものを無くす。

## 4. SaaS_A API の利用

| 用途 | エンドポイント | 備考 |
|---|---|---|
| 認証 | `POST /api/v2/getToken` | 実行のたびにログイン。`token_info.access_token` をメモリ内のみで保持。ログアウト API は無く、トークンは `expires_at` で失効する |
| テナント一覧 | `GET /api/v2/tenant/list` | `tenant_description` / `retention_period` / `device_count`。`page`/`perPage` でページング（`data.count` と `data.rows`） |
| 現場一覧 | `GET /api/v2/group/list` | 現場名の表示順用（`groups[]`） |
| 地点一覧 | `GET /api/v2/device/list` | `device_description` / `format_name` / `format_detail[]` / `group_cds` / `group_names`。`perPage=999` で全ページ取得 |
| 受信有無 | `GET /api/v2/download/export` | **テナント一括**（`group_cd=all&device_cd=all&push_type=csv&output_type=raw_data`）を **7 日窓**で分割して取得（R4 で確定）。CSV は Shift-JIS（`cp932`）。1 回の上限 100,000 行。超過時は HTTP 400 `Error.Export.LimitOver,<件数>,<上限>` |

- 検証環境・本番のそれぞれに専用 API ユーザーを作成する（1 ユーザー 1 トークンのため、人や `saasa-dev` MCP と共用しない）。export は権限レベル「システム管理者・テナント管理者・現場管理者」、全テナント横断にはシステム管理者権限が必要
- 接続先はサイト固定の `TARGET_ENV`（3.7）で決まる。認証情報は SSM Parameter Store SecureString `/example-backoffice/{env}/apps/saas-a-billing/targets/{TARGET_ENV}/base-url` / `user-id` / `password` に置く。**サイトが読むのは固定環境の 1 セットだけ**（dev は `targets/verification/*`、prod は `targets/production/*`）
- `base-url` は `https://` 以外のスキームを受け付けない（読み出し時に検査し、違反は接続前に失敗させる）
- 本番 API（Bearer 版）の公開前は検証環境の URL を `targets/production/base-url` に登録して prod サイトを立ち上げてよい（公開後に `aws ssm put-parameter --overwrite` で差し替える。14.4）

## 5. アーキテクチャ

### 5.1 スタックと配置

| 項目 | 値 |
|---|---|
| スタック名 | `example-backoffice-app-saas-a-billing-{env}`（env ∈ {dev, prod}） |
| 集計対象環境 | パラメータ `TargetEnv`（`verification` / `production`）。dev=`verification` / prod=`production`（3.7）。全 Lambda の環境変数 `TARGET_ENV` |
| リージョン | ap-northeast-1 |
| ランタイム | Python 3.13 / SAM |
| 前提規模 | 当面 〜50 テナント・〜500 地点。将来 2,000〜3,000 地点 |
| Lambda | `api`（HTTP API ハンドラ）、`trigger`（実行判定）、`init` / `collect_tenant` / `aggregate` / `mark_failed`（ステートマシンのタスク） |
| ステートマシン | Step Functions（Standard）`saas-a-billing-run`。収集をテナント分割する |
| ストレージ | DynamoDB `example-backoffice-saas-a-billing-data-{env}`（プラットフォーム設計 14.1 の命名規約）、S3 は core の添付バケット（プレフィックス `saas-a-billing/`） |
| スケジュール | EventBridge Scheduler `cron(0 6 * * ? *)`、`ScheduleExpressionTimezone: Asia/Tokyo`、FlexibleTimeWindow OFF → `trigger` Lambda |
| 通知 | core の共通 notify SQS（shared レイヤの `notify.send`） |
| ポータル URL | prod `https://backoffice.example-cloud.com/saas-a-billing`、dev `https://backoffice-dev.example-cloud.com/saas-a-billing` |
| API ルート | `ANY /api/saas-a-billing/{proxy+}`（JWT authorizer = Cognito） |

### 5.2 バッチ経路

![バッチ経路](images/batch-flow.png)

### 5.3 API 経路

![API 経路](images/api-flow.png)

### 5.4 プラットフォームとの契約（SSM）

| 方向 | キー | 用途 |
|---|---|---|
| 読む（テンプレートパラメータ `AWS::SSM::Parameter::Value<String>`） | `/example-backoffice/{env}/cognito/user-pool-id` | 参照用 |
| 同上 | `/example-backoffice/{env}/cognito/client-id` | JWT authorizer の audience |
| 同上 | `/example-backoffice/{env}/cognito/issuer` | JWT authorizer の issuer |
| 同上 | `/example-backoffice/{env}/notify/queue-url` | `notify.send` の投函先（環境変数 `NOTIFY_QUEUE_URL`） |
| 同上 | `/example-backoffice/{env}/notify/queue-arn` | IAM `sqs:SendMessage` の Resource |
| 同上 | `/example-backoffice/{env}/attachments/bucket` | Excel / result.json の保存先（環境変数 `ATTACHMENTS_BUCKET`） |
| 同上 | `/example-backoffice/{env}/shared/layer-arn` | shared レイヤ |
| 同上 | `/example-backoffice/{env}/ops/topic-arn` | パラメータ `OpsTopicArn`。アラーム 4 本の `AlarmActions` |
| 読む（Lambda 実行時、SecureString） | `/example-backoffice/{env}/edge/origin-verify-secret` | shared の `origin_verify` ミドルウェア |
| 読む（Lambda 実行時、SecureString） | `/example-backoffice/{env}/apps/saas-a-billing/targets/{TARGET_ENV}/base-url` / `user-id` / `password` | SaaS_A API 接続。**サイト固定の 1 セットのみ**（3.7） |
| 読む（Lambda 実行時、String） | `/example-backoffice/{env}/shared/slack/mode` | `GET /settings` が返す `slack_mode`（Bot token 本体は読まない）。書き手は shared スタックだけ（samconfig の `SlackMode`）で、登録スクリプトは書かない |
| 書く（`AWS::SSM::Parameter`） | `/example-backoffice/{env}/apps/saas-a-billing/api-domain` | edge が origin として読む |

- prod は `samconfig.toml` の `[production.deploy.parameters]` でキー名（値ではない）を `/example-backoffice/prod/...` に上書きする。`tests/infra/test_samconfig.py` が dev / prod 両方の `parameter_overrides` を機械検査する
- IAM の `ssm:GetParameter*` は `targets/{TargetEnv}` とその配下の 2 本に限定する。`get_parameters_by_path` はパスそのものの ARN にも権限が要るため、配下（`/*`）だけでは足りない

### 5.5 コード構成

```text
apps/saas-a-billing/
  template.yaml                  # HttpApi, api/trigger/batch Function, StateMachine, Table, ScheduleV2, DLQ, SSM書出し
  statemachine/
    run.asl.json                 # saas-a-billing-run の定義（DefinitionSubstitutions で関数 ARN を注入）
  samconfig.toml                 # [default]=dev, [production]
  functions/                     # 全関数が共有する 1 パッケージ（CodeUri は 1 つ、Handler だけを分ける）
    requirements.txt             # XlsxWriter, requests（boto3/Powertools はランタイム同梱・レイヤ）
    api/app.py                   # APIGatewayHttpResolver。ルート定義のみ
    batch/handlers/
      trigger.py                 # 締め日時と RUN 状態を判定して StartExecution
      init.py                    # RUN 取得・設定/単価解決・getToken・tenant/list 全ページ
      collect_tenant.py          # テナント 1 件の収集 → S3 parts/{tenant_cd}.json
      aggregate.py               # parts 集約・判定・削除地点差分・Excel・S3・RUN 更新・notify
      mark_failed.py             # RUN=failed + エラー要約 + 失敗通知
    saas_a_billing/
      period.py                  # 締め日時・課金対象期間・対象月・窓分割（純粋関数）
      saasa_client.py          # getToken / list / export（requests、ストリーム）
      export_parser.py           # CSV 行ストリーム → 受信あり集合・カメラ集合・初回/最終受信日時
      billing.py                 # 除外/通信なし/課金 判定・金額計算（純粋関数）
      pricing.py                 # 単価表の版解決・単価引き（純粋関数）
      distribution.py            # 配布先判定（mail_enabled / slack_enabled → channels。全送信経路が共用）
      excel.py                   # XlsxWriter で 2 シートを生成（BytesIO）
      repo.py                    # DynamoDB アクセス（SETTINGS/PRICE/RUN/AUDIT）
      storage.py                 # S3 put/get/presign（parts / xlsx / result.json）
      messages.py                # メール件名・本文・Slack 文面の組立
  tests/
    unit/                        # period / billing / pricing / export_parser / excel
    integration/                 # moto + responses で handler を一気通貫
    fixtures/                    # R4 由来の縮小 CSV（cp932）、API 応答 JSON
```

- 6 つの関数（`api` / `trigger` / `init` / `collect_tenant` / `aggregate` / `mark_failed`）は同一 `CodeUri: functions/` を共有し、Handler だけを分ける（ビルド 1 回、共通パッケージの二重管理なし）
- `api`: timeout 29 秒、メモリ 512MB。`trigger`: timeout 60 秒、メモリ 256MB。`init` / `aggregate` / `mark_failed` / `collect_tenant`: timeout 900 秒、メモリ 1024MB。すべてに shared レイヤを付ける

## 6. バッチ処理の詳細

### 6.1 起動イベント

| trigger | 発行元 | ステートマシンへの入力 | distribute | force |
|---|---|---|---|---|
| `scheduled` | EventBridge Scheduler（毎日 06:00 JST）→ `trigger` Lambda → `StartExecution` | `{"trigger":"scheduled","target_env":"production","month":"2026-10","attempt":1,"run_token":"<uuid>"}` | 常に true | 常に false |
| `manual` | api（`POST /runs`）→ `StartExecution` | `{"trigger":"manual","target_env":"verification","month":"2026-09","attempt":1,"distribute":false,"force":false,"run_token":"<uuid>","requested_by":"<cognito sub>","requested_by_email":"<email>"}` | 画面指定（**既定 false**） | 画面指定（既定 false） |

- ステートマシンへの入力には実行名・実行履歴の可読性のために `target_env` を載せるが、**Lambda はこれを読まない**。各ハンドラは環境変数 `TARGET_ENV` を正とする（3.7）
- 実行名は `{target_env}-{YYYY-MM}-{attempt}-{uuid8}`。同一環境・同一対象月・同一試行の二重起動は実行名の重複で弾かれる

### 6.2 実行判定（trigger Lambda）

```text
settings = repo.get_settings()
target   = repo.target_env()                    # 環境変数 TARGET_ENV（サイト固定。3.7）
now      = now(JST)
cutoff   = latest_cutoff(now, settings.cutoff_day, settings.cutoff_time)
           # 現在時刻以前で最も新しい締め日時。cutoff_time が 24:00 なら翌日 00:00
month    = target_month(cutoff)                 # 締め日が属する年月
if not settings.auto_run_enabled:               -> 空振り（INFO ログのみ）
if month < settings.auto_run_from_month:        -> 空振り（導入前の月を拾わない）
run = repo.get_run(target, month)
if run and run.status in (succeeded, distributed):            -> 空振り
if run and run.status in (queued, running) and not stale(run): -> 空振り
if run and run.status == failed and run.auto_attempts >= settings.auto_retry_days:
                                                -> 空振り + 「要手動」アラーム通知（1 回のみ）
acquire_run(target, month, trigger="scheduled") # status=queued, attempt+1, auto_attempts+1
                                        -> StartExecution
```

- 既定（`cutoff_day=20`, `cutoff_time=24:00`）では 21 日 06:00 が最初の実行機会になる。21 日に失敗しても 22 日以降に自動でキャッチアップする
- `failed` の RUN は翌日 06:00 に自動再実行する（1 日 1 回）。スケジュール起動での再実行回数 `auto_attempts` が `auto_retry_days`（既定 5）に達したら自動再実行を止め、「要手動」のアラーム通知を出す（6.8）。手動実行（`force` を含む）は `auto_attempts` を増やさないため、上限枠を消費しない
- 「要手動」アラームは 1 回だけ送り、RUN の `retry_alert_sent` で記録する。**設定の配布先が両方無効な場合は送らず `retry_alert_sent` だけを立てる**（後から配布を有効化しても過去の分が突然飛ばない）
- `acquire_run` 後に `StartExecution` が失敗した場合は RUN を `failed`（`error: "StartExecution に失敗しました"`）に戻す。`queued` のまま残さないため、翌日の自動再実行が `stale` 判定を待たずに走る
- 「未実行の検知」はこの判定で兼ねる。当月の RUN が succeeded にならない限り毎日 06:00 に再評価されるため、別のアラームは作らない
- `stale(run)` = 翌日 06:00 の判定時点でまだ `queued` / `running` のまま残っている RUN（`started_at` が前日以前）。ステートマシンが異常終了して RUN が残った場合に翌日の trigger が奪取する
- 締め日・締め時刻を画面で変えても翌日の判定から反映される（IaC 変更なし）
- スケジュール実行の集計対象環境はサイト固定（`TARGET_ENV`）。設定レコードに別の値が残っていても使わない
- 手動実行（manual）はこの判定を通らず、api が取得済みの RUN（status=queued）を引き継ぐ。scheduled も同じく trigger が RUN を `queued` で取得してから `StartExecution` する（起動経路によらず `queued` → `running` の遷移になる）

### 6.3 ステートマシン `saas-a-billing-run`

| # | ステート | 種別 | 内容 | 失敗時 |
|---|---|---|---|---|
| 1 | `Init` | Task（Lambda） | RUN 取得（条件付き書き込みで二重起動防止）、設定・単価解決、`target_env` の確定（環境変数 `TARGET_ENV`）と当該環境の SSM 3 件の存在確認、`getToken`、トークンを RUN アイテムの属性 `api_token` に保存（`token_expires_at` = `expires_at`）、`tenant/list` 全ページ。出力はテナント ID 配列・`target_env`・`run_id` のみ | 全体 Catch → `MarkFailed`。SSM 3 件が揃わない場合は `TARGET_ENV_NOT_CONFIGURED` で即失敗 |
| 2 | `CollectTenants` | Map（`ItemsPath: $.tenants`、`MaxConcurrency: 2`、`ItemSelector` で `run_id` / `target_env` / `month` / `tenant_id` を各要素に注入） | 各要素で `CollectTenant`（Lambda）を呼ぶ。RUN からトークンを読み、6.4 の収集（device/list, group/list, export の窓分割）を行い、結果を S3 `saas-a-billing/{target_env}/{YYYY}/{MM}/parts/{tenant_cd}.json` に PUT する。戻り値は `tenant_cd` と件数サマリのみ | `Retry`（`States.TaskFailed`、2 回、`BackoffRate: 2.0`）。`Catch` は `ResultPath: $.error` を指定して元入力（`tenant_id` / `tenant_cd` 等）を保持したまま `failed: true` を返し、Map を続行する |
| 3 | `Aggregate` | Task（Lambda） | Map 出力の `tenant_cd` 一覧から parts キーを組み立てて読み込み（S3 の一覧走査に依存しない）、6.5 の判定・集計、削除地点差分、Excel 生成（6.6）、S3 PUT（6.7）、RUN=`succeeded`、`distribute` なら notify（6.8） | 全体 Catch → `MarkFailed` |
| 4 | `MarkFailed` | Task（Lambda） | RUN=`failed` + エラー要約 + 失敗通知 | 通知自体の失敗は CloudWatch ログのみ |

- **Lambda の一時エラー Retry**: `Init` / `Aggregate` / `MarkFailed` の各 Task に `Lambda.ServiceException` / `Lambda.AWSLambdaException` / `Lambda.SdkClientException` / `Lambda.TooManyRequestsException` の `Retry`（最大 3 回、間隔 2 秒、`BackoffRate: 2.0`）を付ける（`CollectTenant` は Map 内の `States.TaskFailed` Retry が兼ねる）。`MarkFailed` に `Catch` は無く、業務失敗でも実行自体は `SUCCEEDED` で終わる
- トークンは同一 API ユーザーで 1 つしか有効にならないため、**ログインは `Init` の 1 回だけ**行う。`CollectTenant` は再ログインしない（401 が出たら当該テナントの失敗として記録する）。`Aggregate` / `MarkFailed` は終了時に RUN の `api_token` / `token_expires_at` 属性を削除する
- トークンはステートマシンの入出力・実行履歴に載せない。RUN アイテム経由で受け渡す（テーブルは KMS SSE）
- 起動は `trigger` Lambda（Scheduler 毎日 06:00）と api（`POST /runs`）の双方が `StartExecution` を呼ぶ
- SAM は `AWS::Serverless::StateMachine`（`DefinitionUri: statemachine/run.asl.json`、`DefinitionSubstitutions` で 4 関数の ARN を注入）で定義する
- IAM: ステートマシンロールに 4 関数（`init` / `collect_tenant` / `aggregate` / `mark_failed`）の `lambda:InvokeFunction`、`trigger` と api のロールに `states:StartExecution`

### 6.4 テナント別収集（CollectTenant）

```text
collect_tenant(tenant, run, period, deadline):
    token   = run.api_token                                        # Init が RUN に保存したトークン
    devices = all_pages(GET device/list, tenant_id, perPage=999)   # data.count に達するまで
    groups  = GET group/list (tenant_id)
    received, camera, first_seen, last_seen = export_received(tenant, run, period, deadline)
    put_s3(f"saas-a-billing/{target_env}/{YYYY}/{MM}/parts/{tenant_cd}.json", TenantPart(...))
    return {"tenant_cd": ..., "devices": n, "received": m, "failed": false}

export_received(tenant, run, period, deadline):
    received = set(); camera = set(); first_seen = {}; last_seen = {}   # point_cd -> change_datetime
    for (ws, we) in windows(period.start, period.end, days=EXPORT_WINDOW_DAYS):   # 既定 7
        fetch_window(ws, we)
    return received, camera, first_seen, last_seen

fetch_window(ws, we):
    if remaining(deadline) < 0: raise TimeBudgetExceeded
    resp = GET download/export?tenant_id&start_date=ws&end_date=we
           &group_cd=all&device_cd=all&push_type=csv&output_type=raw_data (stream=True)
    if resp.status == 400 and resp.message startswith "Error.Export.LimitOver":
        days = (we - ws).days + 1
        if days > 1:
            mid = ws + (days // 2 - 1)
            fetch_window(ws, mid); fetch_window(mid + 1, we)     # 窓を半分にして再帰
        else:
            fetch_per_device(ws, we)                              # 1 日でも超える → 地点単位
        return
    if resp.status == 200 and (body is empty or JSON errorStatus == "Error.Export.NoRecords"):
        return                                                    # 受信なし
    parse_stream(resp, received, camera, first_seen, last_seen)

fetch_per_device(ws, we):
    for device_cd in devices - received:                          # 未確認の地点だけ
        resp = GET download/export?...&group_cd=all&device_cd=<device_cd>
        if resp.status == 400 and LimitOver: received.add(device_cd)   # 上限超過 = 受信あり確定
        elif 200 with body: parse_stream(...)
```

- 窓は日付単位（`start_date`/`end_date` は `YYYY-MM-DD`、両端を含む）。課金対象期間 8/21〜9/20 を 7 日窓で切ると 8/21〜27, 8/28〜9/3, 9/4〜10, 9/11〜17, 9/18〜20 の 5 回
- `cutoff_time` が `24:00` 以外の場合、両端の日は日付単位で取得したうえで `change_datetime` により課金対象期間の内外を判定する（境界日の行を時刻で絞り込む）
- `end_date` が未来日の場合は API 側が現在時刻に丸める（課金対象期間が未了の手動実行でも動く）
- 並列度: Map の `MaxConcurrency: 2` × テナント内の窓は直列 = 同時接続 2（R4 で 4 並列まで問題なし）
- HTTP: `requests` + `Retry(total=3, backoff_factor=1, status_forcelist=[429,500,502,503,504], respect_retry_after_header=True)`、timeout `(5, 120)`。401 はトークン競合（他所で同ユーザーがログイン）の可能性が高いため再試行せず、当該テナントを取得失敗として記録する
- トークンは `Init` が 1 回だけ取得して RUN アイテムに保存し、`CollectTenant` は読み取りのみ行う。ログに出さない
- `deadline` は Lambda の残り時間（`context.get_remaining_time_in_millis()`）から `TIME_BUDGET_MARGIN_SEC` を引いた値。超過したテナントは `failed: true`（時間切れ）として parts に記録する
- 収集結果は S3 の parts（`target_env` 配下）に置き、ステートマシンへの戻り値は件数サマリだけにする（ステート入出力のサイズ上限対策）

#### CSV ストリーム処理（export_parser）

```text
parse_stream(resp, received, camera, first_seen, last_seen):
    lines  = (raw.decode("cp932", errors="replace") for raw in resp.iter_lines())
    reader = csv.reader(lines)                    # 引用符内のカンマ・改行を csv モジュールに任せる
    in_camera_block = False
    for row in reader:
        if not row: continue
        if row[0] == "field_name":                # ブロック境界（ヘッダ行）
            in_camera_block = "Base64形式データ" in row
            continue
        point_cd = row[1]
        if len(row) > 3:
            ts = row[3]                           # "YYYY/MM/DD HH:MM:SS" は文字列比較で可
            if not in_period(ts): continue        # 境界日の時刻絞り込み
            if ts < first_seen.get(point_cd, "9999"): first_seen[point_cd] = ts
            if ts > last_seen.get(point_cd, ""):      last_seen[point_cd] = ts
        received.add(point_cd)
        if in_camera_block: camera.add(point_cd)
```

- 行を保持しない（集合 2 つと辞書 2 つのみ）。7 日窓 12MB / 25 日窓 43MB でもメモリは数 MB
- `field_name`（現場名）は複数現場所属だと `"現場A,現場B"` のように引用符付きで来るため、単純な `split(",")` は使わない
- `point_cd` は `device/list` の `device_cd` と一致する（R4 で確認）

### 6.5 判定・金額計算（純粋関数）

```text
classify(tenant, device, received, camera_by_export, settings) -> Judgement:
    excluded = contains_any(tenant.tenant_description, settings.exclude_keywords)
            or contains_any(device.device_description, settings.exclude_keywords)   # 大文字小文字無視・部分一致
    is_camera = device.device_cd in camera_by_export
             or device.format_name in settings.camera_format_names
    unit_price, flags = resolve_price(price_table, tenant.retention_period,
                                      len(device.format_detail), is_camera)
    if excluded:                      status = "excluded";  amount = 0
    elif device.device_cd not in received: status = "no_data"; amount = 0
    else:                             status = "billable";  amount = unit_price

resolve_price(table, retention, item_count, is_camera) -> (unit_price, flags):
    col = "price_1y" if retention == 1 else "price_3y"
    flags = [] if retention in (1, 3) else ["RETENTION_UNKNOWN"]     # 3 年扱いで仮計算
    if is_camera: return table.camera[col], flags
    if not item_count: return 0, flags + ["NO_ITEMS"]
    for band in table.bands (max_items 昇順):
        if item_count <= band.max_items: return band[col], flags
    return table.bands[-1][col], flags + ["OVER_MAX_ITEMS"]           # 上限帯を仮適用・要確認

boundary_only(first_seen, last_seen, period) -> bool:
    # 受信が課金対象期間の初日だけ、または最終日だけに収まっている
    d1, d2 = date(first_seen), date(last_seen)
    return d1 == d2 and d1 in (period.first_date, period.last_date)

missing_devices(prev_result, current_device_cds) -> list:
    # 同一 target_env の前回 RUN（直前の対象月で succeeded の RUN）の result.json との差分
    if prev_result is None: return []                                # 前回 RUN が無ければ差分なし
    return [d for d in prev_result.devices if d.device_cd not in current_device_cds]
```

| 判定 | 表示色 | 金額 | 備考 |
|---|---|---|---|
| `billable`（課金） | 白 | 単価 | |
| `no_data`（通信なし） | アンバー | 0 | 単価は表示。「課金対象期間に受信データがある機器でデモ機以外を課金」が業務ルール |
| `excluded`（除外） | 青 | 0 | 単価は取り消し線で参考表示 |
| `failed`（取得失敗） | 灰 | 集計外 | テナントの収集に失敗。合計に含めない |
| `missing`（消えた地点） | 灰字 | 計上しない | 前回 RUN にあり今回の `device/list` に無い地点。シート2に列挙し、サマリに件数を出す |
| フラグ `OVER_MAX_ITEMS` / `NO_ITEMS` / `RETENTION_UNKNOWN` | 赤文字 | 上記どおり | 仮単価で課金して合計に含め、「要確認」列に理由を書く。シート1にも載せる |

- 「境界日のみ受信」は判定・金額を変えない。明細の備考に印を付けるだけの補助情報である
- 現行スキル `aggregate_report.py` の `calc_fee` からの移植点: (1) 除外判定の対象を説明欄・備考欄に限定（現行は地点名・地点CDも対象）、(2) 通信なし地点は 0 円（現行は課金加算）、(3) 単価表を DynamoDB の版から受け取る（現行はハードコード）
- テナントの並び: `tenant_cd` 昇順。地点の並び: 先頭 `group_cd` 昇順 → `device_cd` 昇順（照合時に毎月同じ順で並ぶ）

### 6.6 Excel 生成（XlsxWriter）

- `io.BytesIO` に書き、S3 へ PUT。`/tmp` は使わない。全セルの既定書式は `font_name: Meiryo, font_size: 10`
- **ワークブックは `{"strings_to_formulas": False, "strings_to_urls": False}` で開き、文字列セルは `write_string` で書く。** SaaS_A 由来の地点名・備考に `=`・`+`・`-`・`@` で始まる文字列（`{=...}` の配列数式表記を含む）が入っていても数式やハイパーリンクに変換されず、そのまま文字列として書かれる（CSV インジェクション対策）。`write()` の型自動判定に任せない
- 数値は数値型で書き、通貨は `"¥"#,##0`。日時は JST の naive `datetime` で `yyyy/mm/dd hh:mm`。合計は Python 側で計算した値を書く（数式は使わない）
- 色分けは Python 側で判定して静的塗り（条件付き書式に頼らない）
- 列幅は `unicodedata.east_asian_width` で全角=2 として計算した最大幅 + 2
- ファイル名は本番が `saas_a_billing_{YYYY-MM}.xlsx`、検証環境が `saas_a_billing_{YYYY-MM}_verification.xlsx`
- 月の見出し・件名の月は**ゼロ埋めしない**（「2026年3月分」「3/20締め」。`messages.py` と Excel で共通の表記）。処理仕様ブロックの「集計対象」は環境名にホスト名を併記する

#### 処理仕様ブロック（両シートの先頭。設定値から生成する）

```text
SaaS_A 月額課金レポート  2026年10月分
集計処理日時: 2026/10/21 06:03（締め日時: 2026/10/20 24:00）
集計対象: 本番環境（saas-a.example.jp）    ※検証環境のときは「集計対象: 検証環境（verify.saas-a.example.net）」
課金対象期間: 2026/09/21 00:00:00 〜 2026/10/20 23:59:59 に受信データがある地点を課金対象とする（受信日時基準・JST）
除外: テナント説明欄または地点備考欄に「デモ」を含む地点は課金対象外
使用単価表: 2025/09/01 適用版   環境: prod   試行: 1 回目
取得失敗テナント: なし（あれば赤字で「T003 ○○（集計外）」）
合計金額                        ¥312,000   ← 7 行の直下。太字 14pt・右寄せ・"¥"#,##0・薄い黄色 #FFF2CC の塗り
```

**合計金額の強調（両シート共通）**

| 位置 | 書式 |
|---|---|
| 処理仕様ブロックの直下（両シート） | ラベル「合計金額」+ 金額。**太字・14pt**（本文は 10pt）・右寄せ・`"¥"#,##0`・塗り `#FFF2CC`・行高を広げる |
| シート1 合計ブロックの「課金地点」行 | 太字・塗り `#FFF2CC`（サイズは本文どおり） |
| シート2 テナント別サマリ末尾の「合計金額」行 | 同上 |

- **合計金額を先頭ブロックに置く**のは、この帳票を受け取る人が最初に見るのが合計金額だからである。シート1 の合計ブロックまでスクロールしないと分からない状態を避ける。値はシート1 の合計ブロック・RUN の `summary.total_amount`・画面の合計金額カードと必ず一致する
- ラベルは画面と同じ「合計金額」に揃える（「（税抜）」等の付記は付けない）

#### シート1「請求照合」

ベンダーA請求書との突合用。判定が「課金」の地点だけを載せる。A4 横印刷、1 ページ幅に収める、タイトル行の繰り返し印刷、フッタにページ番号。

| ブロック | 内容 |
|---|---|
| 処理仕様 | 上記 |
| 合計 | 課金地点数・合計金額、保存期間別内訳（3年: n 地点 ¥x / 1年: m 地点 ¥y）、カメラ内訳（n 地点 ¥z） |
| テナント（`tenant_cd` 昇順） | 見出し行「■ T001 ○○株式会社 ｜ 保存期間 [3年]（色付きバッジ、太字・やや大きめ）｜ 課金 12 地点 ｜ 小計 ¥12,300」→ 地点行 → 小計行 |
| 地点行の列 | 現場 / 地点CD / 地点名 / フォーマット / 項目数 / 保存期間（色付きセル）/ カメラ / 単価 / 金額 / 要確認。並びは `group_cd` → `device_cd` 昇順 |
| 末尾 | 「課金地点なしのテナント」一覧（例 `T005 △△（全地点デモ）`、`T009 □□（通信なし）`）、使用単価表、脚注（通信なし 0 円・除外ルール・2.5 の差異要因・境界日のみ受信の意味） |

- 要確認（仮単価）の地点は載せたうえで赤字にする。通信なし・除外・取得失敗・消えた地点は載せない

#### シート2「全地点（管理用）」

| ブロック | 内容 |
|---|---|
| 処理仕様 | 上記 |
| 判定別件数 | 課金 / 通信なし / 除外 / 要確認 / 取得失敗 / 消えた地点 |
| テナント別サマリ | テナントCD / テナント名 / 保存期間（色）/ 地点数 / 課金 / 通信なし / 除外 / 要確認 / 取得失敗 / 小計 |
| 全地点明細 | テナントCD / テナント名 / 保存期間（色）/ 現場 / 地点CD / 地点名 / フォーマット / 項目数 / カメラ（export / 設定 / -）/ 最終受信日時 / 判定 / 単価 / 金額 / 要確認 / 備考（`device_description`。境界日のみ受信の印もここに書く） |

- 明細は Excel の「テーブル」（XlsxWriter `add_table`、オートフィルタ付き）にする。サマリが上にあるためウィンドウ枠固定は使わず、テーブル内をスクロールすると列見出しが列番号の位置に表示される Excel の機能に頼る
- 行色は判定で塗る。通信なし `#FDF3DC`、除外 `#DFE9F8`、取得失敗 `#E0E0E0`、消えた地点は灰字 `#808080`。要確認は赤文字 `#C00000`

#### 保存期間の色（両シート共通。テナント見出し・サマリ・明細のすべてに適用）

| 保存期間 | 塗り | 文字色 | 表示 |
|---|---|---|---|
| 3年 | `#C6EFCE` | `#006100` | `3年` |
| 1年 | `#E4DFEC` | `#3F3151` | `1年` |
| 不明（3年扱い） | なし | `#C00000` | `不明(3年扱い)` |

- 保存期間セルは行色より優先して塗る。モノクロ印刷でも文字で判別できる

### 6.7 S3 保存

| オブジェクト | キー | Content-Type |
|---|---|---|
| テナント別中間結果 | `saas-a-billing/{target_env}/{YYYY}/{MM}/parts/{tenant_cd}.json` | `application/json` |
| テナント一覧スナップショット | `saas-a-billing/{target_env}/{YYYY}/{MM}/tenants.json`（`Init` が `tenant/list` の全行を保存し、`Aggregate` がテナント属性の解決に読む。ステート入出力にテナント属性を載せない） | `application/json` |
| Excel | `saas-a-billing/{target_env}/{YYYY}/{MM}/saas_a_billing_{YYYY-MM}[_verification].xlsx` | `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet` |
| 明細 JSON | `saas-a-billing/{target_env}/{YYYY}/{MM}/result.json` | `application/json` |

- バケットは core の添付バケット。アプリは削除しない（照合履歴）。再実行時は同一キーを上書きし、RUN の `attempt` で世代を区別する
- **バケットはバージョニング有効**（プラットフォーム設計 11.2）。再実行の上書きや誤操作から 30 日間は非現行バージョンで戻せる（非現行 30 日で失効。取り残しの delete marker も掃除する）
- **保持方針**

| オブジェクト | タグ | 保持 |
|---|---|---|
| `parts/{tenant_cd}.json`、`tenants.json`（中間ファイル） | `lifecycle=expire-90`（`storage.INTERMEDIATE_TAGGING`） | 90 日で自動削除 |
| `saas_a_billing_*.xlsx`、`result.json`（成果物） | **付けない** | 恒久保存 |

  タグは書き手（`storage.put_part` / `put_tenants`）が `PutObject` の `Tagging` で付ける。S3 のライフサイクルはキー途中の `parts/` をプレフィックスで選べない（キーが `saas-a-billing/{target_env}/{YYYY}/{MM}/parts/...` で月が途中に入る）ため、プレフィックスではなくタグ規則にしている。既存の `lifecycle=expire`（400 日）と同じ方式である
- `parts/` は `CollectTenant` → `Aggregate` の受け渡し用の中間ファイル。再実行時は上書きする
- `{target_env}` はサイト固定値（3.7）。検証環境と本番の成果物を同じプレフィックスに混ぜない
- `{YYYY}/{MM}` は対象月（締め月）。例: 本番の 2026年10月分 → `saas-a-billing/production/2026/10/`

### 6.8 通知（共通 notify）

`notify.send` に渡すメッセージ（スキーマ v1）:

```json
{
  "schema": "example-backoffice.notify.v1",
  "message_id": "<uuid>",
  "app": "saas-a-billing",
  "channels": ["email", "slack"],
  "email": {"to": <settings.mail_to>, "cc": <settings.mail_cc>,
            "subject": "【SaaS_A】月額ライセンス料金一覧 2026年10月分（10/20締め）",
            "text": "<本文>"},
  "attachments": [{"bucket": "<ATTACHMENTS_BUCKET>",
                   "key": "saas-a-billing/production/2026/10/saas_a_billing_2026-10.xlsx",
                   "filename": "saas_a_billing_2026-10.xlsx"}],
  "slack": {"channel": "<settings.slack_channel_id>", "mention": "<settings.slack_mention>",
            "text": "<Slack 文面>"}
}
```

| 種別 | 件名 / 文面 | 差し込み |
|---|---|---|
| 成功メール本文 | 対象月・課金対象期間・テナント数・地点数・課金・通信なし・除外・要確認・取得失敗・消えた地点・合計金額・使用単価表・ポータル URL（`/saas-a-billing/runs/{target_env}/{month}`）。`attempt > 1` は「再送（第 n 回）」を件名末尾に付ける | RUN サマリ |
| 成功 Slack | 設定の Slack 文面テンプレート。既定「{month} の SaaS_A 月額課金レポートを送付しました。ベンダーA請求書との照合を忘れずに。合計 {total}（課金 {billable} 地点）」＋ポータル URL。取得失敗テナントがあれば「取得失敗 N テナント（要再実行）」を追記 | `{month}` `{period}` `{tenants}` `{devices}` `{billable}` `{total}` `{url}` |
| 失敗（Slack + メール） | 「【SaaS_A 月額課金レポート】YYYY年MM月分の集計に失敗しました。原因: <エラー要約>。手動実行: <ポータル URL>」 | RUN.error |
| 自動再実行の上限到達（Slack + メール） | 「【要手動】YYYY年M月分の自動再実行が N 回失敗しました。自動再実行を停止しました。手動実行: <ポータル URL>」（N は `auto_attempts`） | RUN.auto_attempts |

- 集計対象環境が `verification` のときは、メール件名の末尾に `[検証環境]` を付け、Slack 本文の先頭に `【検証環境】` を付け、添付ファイル名を `saas_a_billing_{YYYY-MM}_verification.xlsx` にする。`production` のときはいずれも付けない
- 失敗・未実行のアラーム通知先は課金レポートと同じ Slack チャンネルと `mail_to` にする（別チャンネルは作らない）
- Slack には Excel を添付しない。テキスト + サマリ + ポータル URL のみを投稿する。ポータル URL は Cognito 認証が必要なため、チャンネルの閲覧者のうち権限の無い相手には認証画面が出る（許容する）
- `slack.channel` にはチャンネル ID を渡す（設定画面の「Slack チャンネル ID」の値。プラットフォーム設計 8.2）。`slack.mention` は本文先頭に付く
- **`channels` の決定は `distribution.channels(settings)` に一元化する**: `mail_enabled` が真なら `email`、`slack_enabled` が真かつ `slack_channel_id` が空でなければ `slack` を含める。成功レポート（`Aggregate`）・失敗通知（`MarkFailed`）・「要手動」アラーム（`trigger`）の 3 経路すべてがこの判定を通る
- 有効な配布先が無い（`channels` が空の）ときは投函せず、成功時は RUN に `distribute_note: "配布先が無効のため送付なし"` を残して画面と実行履歴に表示する（状態は `succeeded` 止まり）。投函したときは `notify_channels` に実際のチャンネルを記録する
- Slack の URL は空行で区切る（自動リンク対策）。メールの添付は S3 参照で渡し、送信は core の notifier が行う
- **`distribute=false` の RUN は設定に関わらず一切通知しない**（成功レポートも失敗通知も送らない。状態は `succeeded` 止まり）。実行時フラグが設定より優先される

### 6.9 RUN の状態遷移

![RUN の状態遷移](images/run-states.svg)

| 操作 | DynamoDB 操作 | 条件式 |
|---|---|---|
| 取得（新規） | PutItem RUN（PK=`RUN#{target_env}`、SK=`{YYYY-MM}`、初期 `status = queued`、`attempt = 1`、scheduled なら `auto_attempts = 1`） | `attribute_not_exists(PK)` |
| 取得（再実行、force=false） | UpdateItem RUN → queued, attempt+1（scheduled なら auto_attempts+1） | `#status = failed OR (#status IN (queued, running) AND started_at < :stale)` |
| 取得（再実行、force=true） | UpdateItem RUN → queued, attempt+1（**条件式なし**） | なし。`force` は二重起動防止をバイパスするため、実行中の月に使うと二重実行になり得る。画面は確認ダイアログで警告する |
| manual 引き継ぎ | UpdateItem queued → running | `#status = queued AND run_token = :token AND attempt = :attempt` |
| 実行 ARN 記録 | UpdateItem → `execution_arn` | `#status = running AND attempt = :attempt` |
| トークン保存 / 削除 | UpdateItem → `api_token`, `token_expires_at` を SET / REMOVE | `#status = running AND attempt = :attempt` |
| 進捗 | UpdateItem → `tenants_total`, `tenants_done` | `#status = running AND attempt = :attempt` |
| 成功 | UpdateItem → succeeded + summary + s3 | `#status = running AND attempt = :attempt` |
| 配布 | UpdateItem → distributed + notify_message_id | `#status = succeeded AND attempt = :attempt` |
| 失敗 | UpdateItem → failed + error | `attempt = :attempt` |

- `api_token` / `token_expires_at` は `Init` が書き、`Aggregate` / `MarkFailed` が削除する。ステートマシンの入出力・実行履歴には載せない
- `attempt` は全起動経路で加算する通し番号、`auto_attempts` はスケジュール起動による再実行だけを数える別属性である。`auto_retry_days` の判定には `auto_attempts` を使う

### 6.10 所要時間の見積（R4 実測に基づく）

| 項目 | 実測（検証環境 example-dev、55 地点・受信 39 地点） | 設計値 |
|---|---|---|
| テナント一括 export 7 日窓 | 8.74 秒 / 12.2 MB / 27,835 行 | 1 回 ≈ 9 秒 |
| テナント一括 export 25 日窓 | 24.4 秒 / 43.1 MB / 96,830 行（上限 100,000 行の直下） | 転送 ≈ 1.4〜1.8 MB/s |
| 地点単位 export | 5〜9 秒（0 バイトでも 8 秒） | 固定オーバーヘッド ≈ 6 秒/回 |
| 行レート | ≈ 3,870 行/日（テナント全体）、1 行 ≈ 440 バイト | 5 分間隔機 1 台 ≈ 8,900 行/月 |
| 一覧 API | 未計測 | 1 回 ≈ 1〜2 秒（仮定） |

- 時間予算は **1 テナント当たり Lambda 15 分**（`CollectTenant` の timeout 900 秒）で見る。テナント数は Map で線形にスケールするため、テナントが増えても 1 関数あたりの所要時間は変わらない
- 検証環境相当のテナント 1 件: 一覧 2 回（≈ 3 秒）+ export 5 窓（≈ 45 秒）+ パース（< 1 秒/窓）≈ **50〜60 秒**。15 分予算に対して十分な余裕がある
- テナント内の所要時間の一般式: `T ≈ 6 秒 × 呼出回数 + 0.44 KB × 月間行数 ÷ 1.5 MB/s`。月間 50 万行のテナントなら ≈ 10 回 × 6 秒 + 147 秒 ≈ 3.5 分
- 全体の壁時計時間 ≈ テナント数 ÷ `MaxConcurrency`（2）× 1 テナントの所要時間。前提規模（〜50 テナント、1 テナント 60 秒）で ≈ 25 分。Step Functions Standard の実行時間上限に対して十分な余裕がある
- 1 テナントが 15 分予算を超える見込みになった場合は、(1) テナント内の窓を並列化（同時接続 4 以内）、(2) `EXPORT_WINDOW_DAYS` の縮小、(3) `MaxConcurrency` の引き上げ、の順で拡張する。毎回の実測値は CloudWatch メトリクスに残す（14.2）
- 予算超過時の挙動: `deadline` を過ぎたら新しい窓の取得を始めず、当該テナントを「取得失敗（時間切れ）」として parts に記録する（ハードタイムアウトで RUN が `running` のまま残ることを避ける）

## 7. データ設計

### 7.1 DynamoDB テーブル

| 項目 | 値 |
|---|---|
| テーブル名 | `example-backoffice-saas-a-billing-data-{env}`（プラットフォーム設計 14.1 の命名規約） |
| キー | `PK`（String, HASH）/ `SK`（String, RANGE） |
| 課金 | PAY_PER_REQUEST |
| 暗号化 | KMS SSE（`SSESpecification: SSEEnabled true, SSEType KMS`、AWS 管理キー） |
| バックアップ | PITR 有効、`DeletionPolicy: Retain` |
| GSI | なし（全アクセスが PK 指定 + SK 範囲） |

項目種別（表記 `RUN#production#2026-10` は PK=`RUN#production`、SK=`2026-10` を指す）:

| 種別 | PK | SK | 主な属性 |
|---|---|---|---|
| 設定 `SETTINGS#current` | `SETTINGS` | `current` | `target_env`(S: **読み出し時に環境変数 `TARGET_ENV` の値で上書きされる**。保存もこの値のみ), `cutoff_day`(N), `cutoff_time`(S), `auto_retry_days`(N), `auto_run_enabled`(BOOL), `auto_run_from_month`(S), `mail_enabled`(BOOL), `slack_enabled`(BOOL), `mail_to`(L), `mail_cc`(L), `slack_channel_id`(S), `slack_channel_label`(S), `slack_mention`(S), `slack_template`(S), `exclude_keywords`(L), `camera_format_names`(L), `version`(N), `updated_at`(S), `updated_by`(S), `updated_by_email`(S) |
| 単価表版 `PRICE#EFFECTIVE#{YYYY-MM-DD}` | `PRICE` | `EFFECTIVE#2025-09-01` | `effective_from`(S), `bands`(L of M: `max_items`, `price_3y`, `price_1y`), `camera`(M: `price_3y`, `price_1y`), `note`(S), `created_at`(S), `created_by`(S), `created_by_email`(S) |
| 実行 `RUN#{target_env}#{YYYY-MM}` | `RUN#production` | `2026-10` | `target_env`(S), `saasa_host`(S), `status`(S: queued/running/succeeded/failed/distributed), `trigger`(S), `attempt`(N), `auto_attempts`(N), `run_token`(S), `execution_arn`(S), `api_token`(S: 実行中のみ保持), `token_expires_at`(S), `tenants_total`(N), `tenants_done`(N), `requested_by`(S), `requested_by_email`(S), `distribute`(BOOL), `force`(BOOL), `started_at`(S), `finished_at`(S), `closing_period`(M: from, to, cutoff_day, cutoff_time, open), `price_effective_from`(S), `summary`(M: tenant_count, device_count, billable_count, no_data_count, excluded_count, review_count, failed_tenant_count, missing_count, total_amount), `s3_xlsx_key`(S), `s3_result_key`(S), `notify_message_id`(S), `notify_channels`(L), `distribute_note`(S), `notified_at`(S), `retry_alert_sent`(BOOL: 「要手動」アラーム送付済みか。配布先が無効でスキップした場合も立てる), `request_history`(L of M: `requested_by`, `requested_by_email`, `requested_at`, `trigger`, `distribute`, `force`。**直近 20 件**。誰がいつ実行を要求したかの監査。上書きせず追記し、上限を超えたら古いものから切り捨てる), `error`(S), `metrics`(M: duration_sec, export_calls, export_bytes) |
| 監査 `AUDIT#{ISO時刻}` | `AUDIT` | `2026-08-27T10:00:00+09:00#<uuid>` | `target`(S: settings/price), `actor`(S), `actor_email`(S), `before`(M), `after`(M), `at`(S) |

アクセスパターン:

| 用途 | 操作 |
|---|---|
| 設定取得 | GetItem `SETTINGS/current`。未作成なら既定値（3.3）を `version = 0` で seed して返す |
| 設定更新 | TransactWriteItems: Update `SETTINGS/current`（`version = :expected` の楽観ロック、version+1）+ Put `AUDIT/...` |
| 単価表一覧 | Query PK=`PRICE`, ScanIndexForward=False。**1 版も無ければ既定値（2.3、`effective_from: 2025-09-01`）を自動シードしてから返す** |
| 締め日時点の単価表 | Query PK=`PRICE`, SK <= `EFFECTIVE#{締め日}`, ScanIndexForward=False, Limit=1。1 版も無ければ同じく自動シードしてから引く |
| 単価表追加 | TransactWriteItems: Put `PRICE/EFFECTIVE#...`（`attribute_not_exists(SK)`）+ Put `AUDIT/...` |
| 実行一覧 | Query PK=`RUN#{target_env}`, ScanIndexForward=False, Limit=24（月降順）。既定はサイト固定環境。`?target_env=` を明示したときだけ他方を読む（dev に残る過去の履歴を見るための読み取り専用フィルタ） |
| 実行取得 | GetItem `RUN#{target_env}/{month}` |
| 監査一覧 | Query PK=`AUDIT`, ScanIndexForward=False |

- 単価表の版は追記のみ（物理削除しない）。訂正は新しい `effective_from` の版を追加する
- 明細はテーブルに置かず S3 の `result.json` を参照する
- `SETTINGS#current` が未作成の場合、初回の `GET /settings` で 3.3 の既定値を `version = 0` として seed する。seed 時の `target_env` はサイト固定値、配布トグルは両方無効・`slack_channel_id` 空とし、必須チェックは `PUT /settings` の検証でのみ適用する（設定前でも画面が開ける）。`mail_enabled` / `slack_enabled` 属性を持たない既存レコードは読み出し時に無効（False）として補完する
- 既存レコードの `target_env` がサイト固定値と違っていても、読み出しで固定値に上書きする（過去に別環境で運用していた dev の設定レコードをそのまま使える）
- `api_token` は SSM ではなく DynamoDB の属性として持つ。テーブルは KMS SSE で保管時に暗号化される。`Init` が書き、`Aggregate` / `MarkFailed` が削除する
- 削除地点の差分に使う「前回 RUN」は、**同一 `target_env` の**直前の対象月で `succeeded`（`distributed` を含む）の RUN とする。検証環境と本番の履歴は混ぜない

### 7.2 S3 レイアウト

```text
s3://<core 添付バケット>/
  saas-a-billing/
    production/
      2026/
        10/
          parts/
            {tenant_cd}.json
          tenants.json
          saas_a_billing_2026-10.xlsx
          result.json
        11/
          ...
    verification/
      2026/
        10/
          saas_a_billing_2026-10_verification.xlsx
          result.json
```

### 7.3 result.json スキーマ

```json
{
  "schema": "saas-a-billing.result.v1",
  "target_env": "production",
  "month": "2026-10",
  "closing_period": {"from": "2026-09-21T00:00:00+09:00", "to": "2026-10-21T00:00:00+09:00",
                     "cutoff_day": 20, "cutoff_time": "24:00", "open": false},
  "run": {"attempt": 1, "trigger": "scheduled", "started_at": "2026-10-21T06:00:12+09:00",
          "finished_at": "2026-10-21T06:04:40+09:00", "requested_by": null, "env": "prod",
          "target_env": "production", "saasa_host": "saas-a.example.jp",
          "execution_arn": "arn:aws:states:..."},
  "settings_snapshot": {"exclude_keywords": ["デモ"], "camera_format_names": []},
  "price_table": {"effective_from": "2025-09-01",
                  "bands": [{"max_items": 500, "price_3y": 650, "price_1y": 450},
                            {"max_items": 800, "price_3y": 1000, "price_1y": 700},
                            {"max_items": 1600, "price_3y": 1200, "price_1y": 900},
                            {"max_items": 1700, "price_3y": 1350, "price_1y": 1000}],
                  "camera": {"price_3y": 1800, "price_1y": 1400}, "note": "support-tracker #2001"},
  "summary": {"tenant_count": 12, "device_count": 340, "billable_count": 300, "no_data_count": 25,
              "excluded_count": 15, "review_count": 2, "failed_tenant_count": 0,
              "missing_count": 1, "total_amount": 312000},
  "tenants": [
    {"tenant_id": "<uuid>", "tenant_cd": "xxx", "tenant_name": "...", "tenant_description": "",
     "retention_period": 3, "device_count_api": 55, "status": "ok", "error": null,
     "export_calls": 5, "export_bytes": 55000000, "subtotal": 12345,
     "counts": {"billable": 40, "no_data": 10, "excluded": 5, "review": 0},
     "devices": [
       {"device_cd": "10000021-21-LoggerA-400", "device_name": "...", "group_cds": ["g1"], "group_names": ["現場A"],
        "format_name": "LoggerA-400", "item_count": 984, "device_description": "",
        "is_camera": false, "camera_source": null, "retention": 3,
        "received": true, "first_received_at": "2026/09/21 00:05:00",
        "last_received_at": "2026/10/20 23:55:00", "boundary_only": false,
        "judgement": "billable", "unit_price": 1400, "amount": 1400, "flags": [], "note": ""}
     ]}
  ],
  "missing_devices": [
    {"tenant_cd": "yyy", "tenant_name": "...", "device_cd": "...", "device_name": "...",
     "group_names": ["現場B"], "last_seen_month": "2026-09"}
  ]
}
```

- `judgement`: `billable` / `no_data` / `excluded` / `failed`。`camera_source`: `export` / `settings` / `null`
- `summary.review_count` は**判定を問わず**要確認フラグ（`flags`）が付いた地点数（課金以外の判定の地点も数える）
- `retention` は当該地点に適用した保存期間（`1` / `3` / `null`）。`null` は「不明（3年扱い）」で要確認になる
- `boundary_only` は受信が課金対象期間の初日のみ、または最終日のみだった地点を示す（判定・金額には影響しない）
- `missing_devices` は前回 RUN の result.json にあり今回の `device/list` に無い地点。金額は計上しない。前回 RUN が無ければ空配列
- テナント `status` が `failed` のときは `devices` に `device/list` 取得済み分（受信判定は `failed`）だけを入れる。`device/list` 自体に失敗した場合は空配列
- `closing_period.from` / `to` は半開区間 `[from, to)` の端点である。`cutoff_time: "24:00"` の場合、`to` は翌日 00:00 になる。Excel や画面に出す表示用の期間は `to` から 1 秒引いた `2026/10/20 23:59:59` を使う
- `target_env` は集計対象環境（`verification` / `production`）。`saasa_host` と併せて、どの環境を集計した結果かを成果物だけで判別できるようにする
- 画面の月次内訳はこのファイルをそのまま描画する。翌月の削除地点差分も同一 `target_env` のこのファイルを入力にする

## 8. API 設計

共通仕様: プレフィックス `/api/saas-a-billing`（Lambda 側で strip）。JSON。認証は Cognito **ID トークン**（HTTP API authorizer。Lambda 側でも `token_use == "id"` を再検証する）。`x-origin-verify` 不一致は 403。権限判定は shared レイヤの `auth`（Cognito グループ）。日時は ISO 8601（JST オフセット付き）。

**応答エンベロープ**は shared の `response.py` に従う（プラットフォーム設計 10.2）。成功・失敗のどちらも同じ形で返す。

```json
{"ok": true,  "data": {...}, "error": null, "meta": {...}}
{"ok": false, "data": null,  "error": {"code": "TARGET_ENV_FIXED", "message": "集計対象環境は production に固定されています"}}
```

**スロットリング**: HTTP API の `DefaultRouteSettings` に `ThrottlingBurstLimit: 20` / `ThrottlingRateLimit: 10`（全ルート合計。少人数の社内利用に対して十分で、認証を突破された場合の連打を段階的に抑える）。超過は API Gateway が 429 を返す。

| メソッド | パス | 権限 | リクエスト要点 | レスポンス要点 |
|---|---|---|---|---|
| GET | `/settings` | 全ユーザー | なし | 7.1 の設定属性（未作成なら既定値を `version=0` で seed して返す。`target_env` は常にサイト固定値）+ `saasa_host`（固定環境の base-url からホスト名だけ）+ `targets`（**固定環境 1 件のみ**の SSM 登録状況。例 dev なら `{"verification": true}`）+ `slack_mode`（SSM String `/example-backoffice/{env}/shared/slack/mode` の値。`bot` / `webhook`） |
| PUT | `/settings` | `admin` | 設定属性（`version` 必須）。`target_env` は**送らない**（送るなら固定値と同一に限る） | 更新後の設定。`version` 不一致は 409 `VERSION_CONFLICT`。検証: `target_env` が固定値と異なれば 400 `TARGET_ENV_FIXED`、固定環境の SSM 3 件が未登録なら 400 `TARGET_ENV_NOT_CONFIGURED`、`cutoff_day` 1〜28、`cutoff_time` は `HH:MM`（`24:00` を許容）、`auto_retry_days` 1〜31、`auto_run_from_month` は `YYYY-MM` 形式、`slack_channel_id` は `slack_enabled` のとき必須で `C` / `G` で始まる英数字、`mail_to` は `mail_enabled` のとき必須、宛先はメール形式かつ `@example.co.jp`（許可ドメイン外は 400 `MAIL_DOMAIN_NOT_ALLOWED`）、`slack_template` は許可プレースホルダのみ |
| GET | `/prices` | 全ユーザー | なし | 版の配列（`effective_from` 降順）と `current`（今日時点で有効な版） |
| POST | `/prices` | `admin` | `{effective_from, bands[], camera{}, note}` | 追加した版。同日既存は 409 `ALREADY_EXISTS`。検証: `max_items` 昇順・重複なし、単価は 0 以上の整数 |
| GET | `/runs` | 全ユーザー | `?limit=24&target_env=`（省略時はサイト固定環境） | RUN 一覧（月降順、`target_env` 付き）。stale な queued/running は `status: "failed"`, `error: "TIMEOUT(推定)"` に読み替えて返す。`api_token` などの機微属性と `request_history` は落とす（履歴は `GET /runs/{month}` で返す） |
| GET | `/runs/{month}` | 全ユーザー | `?target_env=`（既定はサイト固定環境）、`?detail=true`（**既定 false**） | RUN のみ。`detail=true` のときだけ `result.json` の内容を含める（明細は大きいため必要時のみ） |
| POST | `/runs` | 全ユーザー | `{month:"YYYY-MM", distribute:bool（**既定 false**）, force:bool（既定 false）}`。`target_env` は**省略可**（送るなら固定値と同一に限る） | 202 `{month, target_env, status:"queued", attempt, execution_arn}`。RUN 取得（6.9）→ ステートマシンを `StartExecution`。`target_env` が固定値と異なれば 400 `TARGET_ENV_FIXED`。取得不可は 409 `ALREADY_RUNNING` / `ALREADY_SUCCEEDED`（force=false）。固定環境の SSM 3 件が未登録なら 400 `TARGET_ENV_NOT_CONFIGURED`。課金対象期間の開始が未来の月は 400 `MONTH_NOT_STARTED`。課金対象期間が未了の月は許可し `closing_period.open=true` を記録。実行者（`sub` / `email`）と要求時刻を RUN の `request_history` に追記する |
| GET | `/runs/{month}/download` | 全ユーザー | `?target_env=`（既定はサイト固定環境） | `{url, expires_at}`。S3 `get_object` の署名付き URL、有効期限 300 秒、`ResponseContentDisposition` でファイル名指定。RUN に `s3_xlsx_key` が無ければ 404 |
| GET | `/audits` | `admin` | なし | 監査レコード一覧（降順、最大 50 件 = `AUDIT_LIST_LIMIT`）。設定・単価の変更履歴の表示に使う |

- `POST /runs` は `StartExecution` に失敗したら RUN を `failed` に戻し 502 を返す。実行名は `{target_env}-{YYYY-MM}-{attempt}-{uuid8}`
- **`distribute` の既定は `false`**。ボディに指定が無ければ配布しない。`POST /runs` は認証済み全ユーザーが呼べるため、既定を「配布する」にしておくと不用意な実行がそのまま社内配布になる
- 署名付き URL は Lambda 実行ロールの一時クレデンシャルで署名するため、クレデンシャル失効で早期無効になる。SPA は取得直後に**アンカー遷移で開く**（`fetch` / XHR を使わないので CSP の `connect-src` に S3 を足さずに済む）。メールや Slack には貼らない
- `distribute=false` で作った成功結果を後から配布したい場合は `distribute=true, force=true` で再実行する（配布専用ルートは設けない）

主なエラーコード:

| code | HTTP | 意味 |
|---|---|---|
| `VALIDATION_ERROR` | 400 | 入力の形式不正（月の形式、数値範囲、宛先の形式など） |
| `TARGET_ENV_FIXED` | 400 | 固定と異なる集計対象環境が指定された（3.7）。画面は「集計対象環境はこのサイトで固定されています。画面を読み込み直してから保存してください。」と表示する |
| `TARGET_ENV_NOT_CONFIGURED` | 400 | 固定環境の SSM 3 件が未登録 |
| `MAIL_DOMAIN_NOT_ALLOWED` | 400 | `PUT /settings` の `mail_to` / `mail_cc` に許可ドメイン外（`@example.co.jp` 以外）の宛先が含まれる。判定は notifier と同じ規則（小文字化・`@` 1 個・ドメイン完全一致） |
| `MONTH_NOT_STARTED` | 400 | 課金対象期間の開始が未来 |
| `NOT_FOUND` | 404 | 指定の RUN / Excel が無い |
| `ALREADY_RUNNING` / `ALREADY_SUCCEEDED` | 409 | RUN を取得できない（`force=false`） |
| `VERSION_CONFLICT` | 409 | 設定の楽観ロック不一致 |
| `ALREADY_EXISTS` | 409 | 同じ `effective_from` の単価表版が既にある |
| `START_EXECUTION_FAILED` | 502 | ステートマシンを起動できなかった |

## 9. 画面一覧

SPA 配置: `frontend/src/apps/saas-a-billing/`。マニフェスト（プラットフォーム設計 9.4 の `AppManifest`）は `id: "saas-a-billing"`、`title: "SaaS_A 月額課金レポート"`、`icon: "mdi-receipt-text-outline"`、`order: 10`、`requiredGroups: []`（全ログインユーザー。設定画面だけルート `meta` で `["admin"]`）、`routes`（`/saas-a-billing` 配下の子ルート。`component` はすべて `() => import()`）、`nav`（実行履歴 / 設定）とする。API は `/api/saas-a-billing`（`createAppApi('saas-a-billing')`）。UI デザインは後で詰める。

| 画面 | パス | 主要項目 | 使う API |
|---|---|---|---|
| 実行履歴一覧 | `/saas-a-billing` | 集計対象環境バッジ（検証=オレンジ / 本番=緑。**絞り込みは無い**）、月、状態（色付き）、実行日時、合計金額（太字）、配布状況、Excel ダウンロード、内訳カウント 8 列（テナント数・地点数・通信なし・除外・要確認・取得失敗・消えた地点・試行回数）、次回自動実行予定（設定から算出）、更新ボタンと最終更新時刻 | `GET /runs`, `GET /runs/{month}/download` |
| 月次内訳 | `/saas-a-billing/runs/{target_env}/{month}` | 先頭に**合計金額のサマリカード**（大きく太字。環境バッジを併置）、件数サマリ、テナント → 現場 → 地点のツリー表（Excel と同じ色分け・合計行付き）、消えた地点の一覧、フィルタ（判定・要確認のみ・境界日のみ受信）、判定の凡例、使用単価表、Excel ダウンロード、再実行ボタン、「実行履歴へ戻る」 | `GET /runs/{month}`, `GET /runs/{month}/download`, `POST /runs` |
| 手動実行 | `/saas-a-billing/run`（モーダル可） | 固定の集計対象環境をチップで表示（選択不可。接続先ホストと「このサイトでは固定です」の注記）、対象月（既定: 直近の締め月）、配布する/しない（既定: しない）、強制再実行、確認ステップ | `POST /runs`, `GET /settings` |
| 設定 | `/saas-a-billing/settings` | 3.3 の項目、固定の集計対象環境と接続先ホスト（サマリカードに一本化）、単価表の版一覧と新版追加（適用開始日）、変更履歴（監査。項目名は日本語表示）、Slack の動作方式（Bot token / Webhook フォールバック）の表示。`admin` 以外は入力を `disabled` にした読み取り専用 | `GET/PUT /settings`, `GET/POST /prices` |

**実行・再実行の確認（設計 3.2 / 8 章）**

| 状況 | 画面の要求 |
|---|---|
| 常時 | 集計対象環境（固定）と対象月を確認ステップで再掲する |
| 再実行 | 「既存の結果を上書きする」旨の警告を常時表示する。`force` は既定 OFF の明示チェック（ON にすると二重起動の警告を追加） |
| 実行中・待機中の月 | 再実行ボタンを無効化し、理由を添える |
| 配布 ON | **実際の宛先（メール宛先・Slack チャンネル）を表示し、確認チェックを必須にする**。本番サイトで配布 ON のときは実行ボタンを error 色 +「本番へ配布して実行」にする |

- 一覧・内訳とも表は横スクロール可能なコンテナに入れ、狭い画面では副次列（内訳カウント等）を隠す。合計金額・配布状況・ダウンロード・判定・単価・金額は常に見える
- 受付・失敗・保存結果はスナックバーで通知する

## 10. 設定項目と保管先

| 区分 | 項目 | 保管先 | 変更方法 |
|---|---|---|---|
| 画面設定 | 締め日、締め時刻、自動再実行上限日数、自動実行 有効/無効、自動実行の開始月、メール宛先、Slack チャンネル ID、Slack チャンネル表示名、Slack メンション、Slack 文面、除外キーワード、カメラ扱いフォーマット名 | DynamoDB `SETTINGS#current` | 設定画面（`admin`）。監査記録あり |
| 画面設定 | 単価表（版） | DynamoDB `PRICE#EFFECTIVE#*` | 設定画面（`admin`）。追記のみ |
| **デプロイ時固定** | 集計対象環境（`target_env`） | スタックパラメータ `TargetEnv` → 環境変数 `TARGET_ENV`（3.7） | `samconfig.toml`。**画面・API からは変更できない** |
| 秘密 | SaaS_A API ベース URL / ユーザー ID / パスワード（サイト固定環境の 1 セット） | SSM SecureString `/example-backoffice/{env}/apps/saas-a-billing/targets/{TARGET_ENV}/{base-url,user-id,password}` | AWS CLI / コンソール（手順 14.1）。Lambda 実行時に Powertools `get_parameters(..., decrypt=True, max_age=300)` |
| 秘密 | origin-verify シークレット、Slack Bot token / Webhook URL | SSM SecureString `/example-backoffice/{env}/edge/origin-verify-secret`、`/example-backoffice/{env}/shared/slack/{bot-token,webhook-url}` | core 担当 |
| 固定（環境変数） | `ENV`, `TARGET_ENV`（`verification` / `production`。`TargetEnv` パラメータ由来。**全 6 関数**）, `APP_NAME=saas-a-billing`, `TABLE_NAME`, `ATTACHMENTS_BUCKET`, `S3_PREFIX=saas-a-billing`（実際のキーは `{S3_PREFIX}/{TARGET_ENV}/...`）, `NOTIFY_QUEUE_URL`, `STATE_MACHINE_ARN`（api / trigger）, `PORTAL_URL`, `API_PREFIX=/api/saas-a-billing`, `SSM_PREFIX=/example-backoffice/{env}/apps/saas-a-billing`, `EXPORT_WINDOW_DAYS=7`, `TIME_BUDGET_MARGIN_SEC=150`, `POWERTOOLS_SERVICE_NAME`, `LOG_LEVEL` | template.yaml | デプロイ。export の行上限（100,000）は環境変数に持たず、400 応答 `Error.Export.LimitOver,<件数>,<上限>` から読む |
| 固定（スタックパラメータ） | `TargetEnv`（3.7）、`OpsTopicArn`（SSM 型 `/example-backoffice/{env}/ops/topic-arn`。アラーム 4 本の通知先）、`PortalUrl`、SSM 型の Cognito / notify / attachments / shared-layer 各キー | template.yaml + samconfig.toml | デプロイ。`tests/infra/test_samconfig.py` が dev / prod の値を検査する |
| 固定（IaC） | Scheduler cron `cron(0 6 * * ? *)`、ステートマシン定義（Map の `MaxConcurrency: 2`、`Retry` / `Catch`）、timeout / メモリ、DLQ、EventInvokeConfig、HTTP API のスロットリング（burst 20 / rate 10） | template.yaml / statemachine/run.asl.json | デプロイ |

- 環境変数に秘密を置かない。`{{resolve:ssm-secure}}` は Lambda 環境変数に使えない

## 11. エラー処理・冪等性・再実行

### 11.1 事象別の挙動

| 事象 | 検知 | 挙動 | 記録 | 通知 |
|---|---|---|---|---|
| Scheduler の Invoke 失敗（権限・スロットル） | Scheduler RetryPolicy（2 回・1 時間）で再試行後 DLQ | 翌日 06:00 に再評価される | Scheduler DLQ | DLQ 件数アラーム（14.2） |
| trigger の未処理例外 | Lambda 非同期 Invoke の失敗 | `EventInvokeConfig.MaximumRetryAttempts: 0`（翌日のキャッチアップが再試行の役割）。`OnFailure` → trigger 失敗 SQS | RUN は作られない | Lambda Errors アラーム |
| `Init` の失敗（SSM 取得 / getToken 401 / tenant/list） | ステートマシンの Catch | `MarkFailed` へ | RUN=`failed`, `error` | Slack + `mail_to` に失敗通知 |
| 固定環境の接続情報が未登録 | 設定保存時（`PUT /settings`）・手動実行時（`POST /runs`）・`Init` | 400 `TARGET_ENV_NOT_CONFIGURED` で実行させない。`Init` まで到達した場合は `MarkFailed` へ | RUN=`failed`, `error` | Slack + `mail_to` に失敗通知 |
| 固定と異なる集計対象環境の指定 | `PUT /settings`・`POST /runs` | 400 `TARGET_ENV_FIXED`。RUN は作らない | なし | なし |
| `CollectTenant` の失敗（device/list・group/list・export、再試行後） | Map の `Retry`（2 回）→ `Catch`（`ResultPath: $.error`） | 元入力（`tenant_id` / `tenant_cd`）を保持したまま `failed: true` を返し、Map は続行。`Aggregate` はその `tenant_cd` を取得失敗として集計する | result.json の `tenants[].error`、`failed_tenant_count` | 成功通知に「取得失敗 N テナント」 |
| export 400 LimitOver | `CollectTenant` | 窓を半分に再帰、1 日で超えれば地点単位 | `export_calls` | なし |
| 時間予算超過（1 テナント 15 分） | `deadline` 監視 | 当該テナントを `failed（時間切れ）` として parts に記録、Map は続行 | 同上 + `error: "TIME_BUDGET_EXCEEDED"` | 成功通知に明記 |
| 単価表なし（締め日以前の版が無い） | `Init` | `MarkFailed` へ | RUN=`failed` | Slack + `mail_to` に失敗通知 |
| `Aggregate` の失敗（S3 PUT / RUN 更新 / Excel 生成） | ステートマシンの Catch | `MarkFailed` へ | RUN=`failed` | Slack + `mail_to` に失敗通知 |
| notify 投函失敗（SQS） | `Aggregate` | 状態は `succeeded` のまま、`error` に記録 | RUN | CloudWatch ログ + Errors メトリクス |
| 有効な配布先が無い（`mail_enabled` / `slack_enabled` とも無効、または Slack のみ有効でチャンネル ID 空） | `Aggregate` | 投函せず成功扱い（失敗にはしない） | RUN の `distribute_note: "配布先が無効のため送付なし"` | 画面と実行履歴に表示 |
| 自動再実行の上限到達（`auto_attempts` >= `auto_retry_days`） | `trigger` の判定 | スケジュールによる自動再実行を止める（手動実行は可能） | RUN=`failed`, `auto_attempts`, `retry_alert_sent` | 「要手動」アラーム（設定で有効な配布先へ、1 回のみ。配布先が無ければ送らず `retry_alert_sent` のみ立てる） |
| 失敗通知自体の失敗 | `MarkFailed` | ログのみ | ログ | なし |

- 表中の失敗通知・成功通知はいずれも 3.6 / 6.8 の配布ルールに従う: 設定で有効な配布先（`distribution.channels`）にのみ送り、`distribute=false` の RUN では一切送らない

### 11.2 冪等性

- 業務キー = 集計対象環境 + 対象月。`RUN#{target_env}` / `{YYYY-MM}` への条件付き書き込み（6.9）で二重起動・二重送信を防ぐ。サイト固定のため実運用では 1 環境しか使わないが、キーの形は変えない（dev に残る過去の履歴と共存する）
- ステートマシンの実行名を `{target_env}-{YYYY-MM}-{attempt}-{uuid8}` にし、同一環境・同一対象月・同一試行の重複起動を弾く
- `force=true` の再実行は条件式なしの UpdateItem で RUN を奪うため、二重起動防止をバイパスする。実行中の月に対して使わないよう画面の確認ダイアログで警告する
- Scheduler の毎日起動は「対象月の RUN が成功済みなら空振り」で冪等
- Lambda 非同期呼び出しの再試行は 0 回に設定し、再試行由来の二重実行を作らない
- Map の `Retry` は `CollectTenant` にのみ効く。`CollectTenant` は parts を上書きするだけなので再試行しても結果は同じになる
- 成果物（S3 キー）は集計対象環境と対象月で固定。再実行は上書き。RUN の `attempt` と `started_at` で世代を判別する
- 単価は「締め日時点で有効な版」を引くため、再実行しても同じ金額になる（遡及訂正の版を追加した場合だけ変わる）
- 宛先・Slack チャンネルは「実行時点の設定」を使う。使った値は result.json の `settings_snapshot` と RUN に残す

### 11.3 再実行

| ケース | 操作 | 結果 |
|---|---|---|
| 失敗後 | 翌日 06:00 の自動再実行（最大 `auto_retry_days` 回）、または画面から `POST /runs`（force 不要） | `attempt + 1`、成功すれば通常配布 |
| 自動再実行の上限到達後 | 「要手動」通知を受けて画面から `POST /runs` | `attempt + 1`。原因を解消してから実行する |
| 成功済みの再集計・再送 | 画面から `force=true`（上書き警告 + 明示チェック。配布 ON なら宛先の確認チェックも必須） | `attempt + 1`。件名末尾に「再送（第 n 回）」 |
| 配布せず確認だけ | `distribute=false` | `succeeded` 止まり。配布は `distribute=true, force=true` で再実行 |
| 特定テナントだけ取得失敗 | 画面から `force=true` で月全体を再実行 | テナント単位の再実行は設けない（月全体が 1 成果物） |

## 12. セキュリティ

### 12.1 認証・認可

- SPA → HTTP API: Cognito ID トークン（`Authorization: Bearer`。プラットフォーム設計 6.3 に従う）。JWT authorizer が issuer / audience を検証し、Lambda 側でも `token_use == "id"` を再検証する
- レート制限: HTTP API のスロットリング（burst 20 / rate 10。8 章）
- CloudFront 以外からの直叩き防止: shared の `origin_verify` ミドルウェアが `x-origin-verify` を検証（SSM SecureString と比較）
- グループ判定: `PUT /settings`, `POST /prices` は `admin` グループのみ。閲覧・実行（`GET *`, `POST /runs`）は認証済み全ユーザー
- 監査: 設定・単価の変更は `AUDIT` に変更者（sub / email）と前後の値を残す。手動実行は RUN の `requested_by` と `request_history`（誰がいつ実行を要求したかの直近 20 件。7.1）に残す。HTTP API のアクセスログに `sub` を出す

### 12.2 IAM 最小権限

api ロール:

| アクション | Resource | 備考 |
|---|---|---|
| `dynamodb:GetItem, Query, PutItem, UpdateItem, TransactWriteItems` | テーブル ARN | |
| `s3:GetObject` | `arn:aws:s3:::{bucket}/saas-a-billing/*` | 署名付き URL と result.json 取得 |
| `states:StartExecution` | ステートマシン ARN | 手動実行 |
| `ssm:GetParameter(s)` | `parameter/example-backoffice/{env}/edge/origin-verify-secret`、`parameter/example-backoffice/{env}/apps/saas-a-billing/targets/{TargetEnv}` と同配下、`parameter/example-backoffice/{env}/shared/slack/mode` | 接続先ホストの表示と登録状況の確認に固定環境の `targets/{TargetEnv}/base-url`、`slack_mode` の返却に `/shared/slack/mode` を読む（Bot token 本体と他方の環境は読まない） |
| `kms:Decrypt` | `alias/aws/ssm` のキー | SecureString 復号 |
| `logs:CreateLogStream, PutLogEvents` | 自関数のロググループ | |

trigger ロール:

| アクション | Resource | 備考 |
|---|---|---|
| `dynamodb:GetItem, PutItem, UpdateItem` | テーブル ARN | SETTINGS 参照と RUN 取得 |
| `states:StartExecution` | ステートマシン ARN | |
| `sqs:SendMessage` | notify キュー ARN | 「要手動」アラーム通知 |
| `logs:CreateLogStream, PutLogEvents` | 自関数のロググループ | |

batch ロール（`init` / `collect_tenant` / `aggregate` / `mark_failed`。関数ごとに作る）:

| アクション | Resource | 備考 |
|---|---|---|
| `dynamodb:GetItem, Query, PutItem, UpdateItem` | テーブル ARN | |
| `s3:PutObject, PutObjectTagging, GetObject` | `arn:aws:s3:::{bucket}/saas-a-billing/*` | `{target_env}/` 配下の parts / xlsx / result.json。中間ファイルの `lifecycle=expire-90` タグ付与（6.7）に `PutObjectTagging` が要る。削除権限は付けない |
| `s3:ListBucket` | `arn:aws:s3:::{bucket}`（**条件なし**。core の `LocateAttachments` と同形） | 欠損キーの GetObject を 403 でなく 404 で判定するために必要。`s3:prefix` 条件を付けると 404 判定に効かないため付けない |
| `sqs:SendMessage` | notify キュー ARN（SSM `/notify/queue-arn`） | `aggregate` / `mark_failed` のみ |
| `ssm:GetParameter(s)` | `parameter/example-backoffice/{env}/apps/saas-a-billing/targets/{TargetEnv}` と同配下（`/*`） | 固定環境の base-url / user-id / password（**`init` と `collect_tenant`**。`aggregate` / `mark_failed` には SSM / KMS 権限を付けない）。パス本体と配下の 2 本を並べるのは `get_parameters_by_path` がパスそのものの ARN も要求するため |
| `kms:Decrypt` | `alias/aws/ssm` のキー | `init` / `collect_tenant` のみ |
| `cloudwatch:PutMetricData` | `*`（`cloudwatch:namespace` 条件で `example-backoffice/saas-a-billing` に限定） | Powertools Metrics（`collect_tenant` / `aggregate` のみ） |
| `logs:CreateLogStream, PutLogEvents` | 自関数のロググループ | |

- 関数ごとの差分: `mark_failed` は S3 権限を持たない（テーブル更新と notify 投函のみ）。`init` / `collect_tenant` / `aggregate` の S3 は `PutObject` / `PutObjectTagging` / `GetObject`（削除権限なし）

ステートマシンロール: `lambda:InvokeFunction` を 4 関数（`init` / `collect_tenant` / `aggregate` / `mark_failed`）の ARN に限定。CloudWatch Logs へ実行ログを出す権限を付ける。

Scheduler ロール（SAM `ScheduleV2` が生成）: `lambda:InvokeFunction` を `trigger` 関数 ARN に限定、`sqs:SendMessage` を Scheduler DLQ に限定。

### 12.3 秘密情報の取り扱い

- SaaS_A の認証情報は SSM SecureString のみ。Lambda 実行時に取得し、ローカル変数で保持。モジュールグローバルに置かない
- トークン・パスワード・`Authorization` ヘッダをログに出さない（Powertools Logger に対象キーを渡さない。`requests` のデバッグログは無効）
- `MarkFailed` はエラー要約に RUN の `api_token` が混入していれば `***` に伏せてから記録・配布する（HTTP エラー文字列が `Authorization` ヘッダを含むケースへの防衛。パスワードは接続情報オブジェクトの `repr` 秘匿が上流の防衛線）
- 設計書・README・チケットに認証情報を書かない
- 専用 API ユーザーは MFA なしの連携用ユーザーとし、人の運用ユーザー・`saasa-dev` MCP のユーザーと分ける。検証環境・本番のそれぞれに用意する

### 12.4 データ保護

- DynamoDB: KMS SSE、PITR。S3: core バケットの設定に従う（SSE、パブリックアクセス禁止）。Excel は請求照合情報のため社外に出さない
- 署名付き URL は 300 秒。メール・Slack に URL を貼らない（ポータル URL のみ）

## 13. テスト方針

| 層 | 対象 | ツール | 内容 |
|---|---|---|---|
| unit | `period.py` | pytest | 対象月の決定（`cutoff_day` 1〜28、`cutoff_time` の `24:00` と任意時刻、月跨ぎ・年跨ぎ）、課金対象期間の境界（開始は含む・終了は含まない。`to` は半開区間の端点）、7 日窓の分割、窓の半分割（奇数日数） |
| unit | `pricing.py` | pytest | 帯の境界（500/501, 1700/1701）、保存期間 1/3/不明、カメラ、項目数 0、版の解決（締め日と同日・前日・未来版の無視） |
| unit | `billing.py` | pytest | 除外 > 通信なし > 課金の優先順位、キーワードの部分一致・大文字小文字、カメラ判定の OR、境界日のみ受信の判定、削除地点の差分（前回 RUN なし・あり）、テナント小計・合計 |
| unit | `export_parser.py` | pytest + フィクスチャ | R4 の実測 CSV を縮小した cp932 フィクスチャ（複数ブロック、カメラブロック、引用符付き現場名、引用符内カンマ）で受信集合・カメラ集合・初回/最終受信日時を検証。境界日の時刻絞り込み。空ボディ・NoRecords JSON |
| unit | `saasa_client.py` | `responses` | ページング（`data.count` 到達）、LimitOver 400 → 半分割の呼出順序、1 日でも超過 → 地点単位、地点単位 LimitOver → 受信あり、429/5xx 再試行、401 は再試行しない |
| unit | `excel.py` | pytest + openpyxl 読み戻し | シート名（請求照合 / 全地点）、処理仕様ブロック（合計金額の行と強調書式を含む）、列見出し、数値型・書式、保存期間の色、判定の行色、Meiryo、合計値、取得失敗行、消えた地点行、テーブル（オートフィルタ）、`=` で始まる文字列が数式化されないこと |
| unit | `messages.py` | pytest | 件名・本文・Slack 文面の差し込み、メンションの付与、再送表記、取得失敗の追記、上限到達時の「要手動」文面 |
| integration | `batch/handlers/trigger.py` + `repo.py` | `moto` | 締め日時の判定（実行機会あり／なし）、自動実行無効、`auto_run_from_month` より前の月の空振り、成功済みの空振り、`stale` 判定（前日以前の queued/running の奪取）、`failed` の翌日再実行、`auto_attempts` が `auto_retry_days` 到達で停止 + 通知、手動 `force` が `auto_attempts` を増やさないこと、環境変数 `TARGET_ENV` を入力に載せること、`StartExecution` の呼び出し |
| integration | 集計対象環境の固定（3.7） | `moto` + `responses` | `GET /settings` が固定値を返すこと（保存値が違っても上書きされること）、`PUT /settings` の `TARGET_ENV_FIXED` と省略時の通過、`POST /runs` の不一致 400・一致許容・省略可、trigger / init / aggregate が環境変数を正とすること、固定環境の SSM 解決と未登録時の `TARGET_ENV_NOT_CONFIGURED`、RUN キー（`RUN#verification` / `RUN#production`）の分離、S3 プレフィックスの分離、削除地点差分が同一環境内で行われること、検証環境時の件名 `[検証環境]`・Slack 先頭 `【検証環境】`・添付名 `_verification` |
| integration | テンプレート・samconfig | pytest（`tests/infra/`） | `TargetEnv` パラメータと `TARGET_ENV` 環境変数、SSM の IAM リソース形、アラーム 4 本の `AlarmActions`、HTTP API のスロットリング、samconfig の dev=`verification` / prod=`production` |
| integration | `batch/handlers/*` | `moto`（DynamoDB/S3/SQS/SSM）+ `responses` | `Init` の条件付き書き込み競合とトークン保存、`CollectTenant` の parts 出力と失敗記録、`Aggregate` の集約・削除地点差分・S3 出力・SQS 投函、`MarkFailed` の RUN 更新と通知 |
| integration | ステートマシン定義 | `sam validate --lint` / Step Functions Local | `run.asl.json` の構文、Map の `MaxConcurrency` / `Retry` / `Catch`、全体 Catch → `MarkFailed` |
| integration | `api/app.py` | `moto` + Powertools のイベント生成 | ルートごとの権限（admin / 一般）、楽観ロック、409/400/404、`StartExecution` の入力、署名付き URL の有効期限 |

- TDD: 純粋関数（period / pricing / billing / export_parser）のテストを先に書き、失敗を確認してから実装する
- カバレッジ目標 80% 以上（`pytest --cov=saas_a_billing`）
- フィクスチャは R4 の `tenant_all_0814_0820.csv` から各ブロック先頭 2〜3 行を抜粋して作る（地点CD・現場名の実データを含むため社外に出さない）
- 突合テスト（14.1）: dev で手動実行した結果を現行スキル `aggregate_report.py` の出力と地点単位で比較し、6.5 の移植点による差だけが残ることを確認する

## 14. 運用

### 14.1 初回導入手順

| # | 作業 | 環境 | 内容 |
|---|---|---|---|
| 1 | SaaS_A 検証環境の専用 API ユーザー作成 | dev | MFA なしの連携用ユーザー。システム管理者権限。`saasa-dev` MCP のユーザーとは別 |
| 2 | SSM 登録（dev、検証環境向け） | dev | `/example-backoffice/dev/apps/saas-a-billing/targets/verification/base-url`（`https://verify.saas-a.example.net`）、`user-id`、`password` を SecureString で登録 |
| 3 | Slack の準備 | dev / prod | プラットフォームの初期セットアップ（プラットフォーム設計 13.4）で Slack App と Bot token を用意する（`scripts/register-slack.sh {env} bot`。shared のデプロイの前後どちらでもよい）。`#proj-saas-a-billing` のチャンネル ID を Slack で確認して設定画面に登録する |
| 4 | デプロイ（dev） | dev | `sam build --use-container && sam deploy`。`/example-backoffice/dev/apps/saas-a-billing/api-domain` が書かれることと、ステートマシン `saas-a-billing-run` が作られることを確認 |
| 5 | edge へ origin/behavior 追加、SPA 配置 | dev | 基盤の手順に従う |
| 6 | 初期設定 | dev | 集計対象環境は `verification` に固定済み（3.7。画面では表示のみ）。設定画面で宛先（`devA@example.co.jp`）、Slack チャンネル ID（テスト用）、締め日 20 / 締め時刻 24:00、単価表の初版（`effective_from: 2025-09-01`、#2001 の値）を登録。自動実行は無効のまま |
| 7 | 9 月分を手動実行（配布しない） | dev | `POST /runs {month:"2026-09", distribute:false}`。ステートマシンの実行、画面、ダウンロードした Excel（2 シート）を確認 |
| 8 | 現行スキルと突合 | dev | 同じ期間を `saas-a-billing-report` スキルで集計し、地点単位で比較。差異が 6.5 の移植点で説明できることを確認 |
| 9 | 削除地点差分の確認 | dev | 連続する 2 か月分を実行し、消えた地点がシート2に灰字で出ること、金額に影響しないことを確認 |
| 10 | 配布テスト | dev | `distribute:true, force:true` で再実行し、メール（添付）と Slack（添付なし・メンション）を受信確認 |
| 11 | 本番 API ユーザー作成 | prod | 本番 API（Bearer 版）リリース後に作成。権限・MFA なしは dev と同じ。公開前は検証環境の専用ユーザーで代替する |
| 12 | SSM 登録（prod） | prod | **`/example-backoffice/prod/apps/saas-a-billing/targets/production/{base-url,user-id,password}` の 3 本だけ**を登録する（prod は `production` 固定なので `targets/verification/*` は読まれない）。本番 API 公開前は base-url に検証環境の URL を入れておき、公開後に `--overwrite` で `https://saas-a.example.jp` に差し替える（14.4） |
| 13 | デプロイ（prod） | prod | `sam deploy --config-env production`（`TargetEnv=production` / `OpsTopicArn=/example-backoffice/prod/ops/topic-arn`）→ edge → frontend。**core が `/ops/topic-arn` を書いた後**でないとパラメータの解決に失敗する |
| 14 | 本番初期設定 | prod | 集計対象環境は `production` に固定済み（画面では表示のみ）。宛先（`devA@example.co.jp`）、Slack チャンネル ID（`#proj-saas-a-billing` の ID）、単価表初版、除外キーワード。`auto_run_from_month: 2026-10` |
| 15 | 本番の事前検証 | prod | 10/1 以降に 9 月分を `distribute:false` で手動実行し、テナント数・地点数と 1 テナントあたりの所要時間（`metrics.duration_sec`）を確認。1 テナント 15 分予算に対する余裕を判定 |
| 16 | 自動実行の有効化 | prod | **前提: アラーム 4 本（14.2）が Ops トピックに接続され、そのトピックのメール購読が Confirmed であること**（プラットフォーム設計 13.4 の手順 6）。`aws cloudwatch describe-alarms` で `AlarmActions` が 1 以上、`aws sns list-subscriptions-by-topic` で購読が Confirmed であることを確認してから、設定画面で `auto_run_enabled: true`。10/21 06:00 に初回自動実行 |
| 17 | 初回自動実行の確認 | prod | 10/21 にメール・Slack・画面を確認。ベンダーAの請求書と照合 |

### 14.2 監視

| 対象 | 手段 | しきい値 / 内容 |
|---|---|---|
| ステートマシンの失敗 | CloudWatch Alarm（Step Functions `ExecutionsFailed` / `ExecutionsTimedOut` ≥ 1、**期間 300 秒**、`EvaluationPeriods: 1`、`TreatMissingData: notBreaching`） | `MarkFailed` を通らない異常終了も拾う |
| Lambda の失敗 | CloudWatch Alarm（自スタック 6 関数の `Errors` の合算 ≥ 1、期間 300 秒） | タイムアウトも Errors に計上される |
| Scheduler DLQ / trigger 失敗 SQS | CloudWatch Alarm（`ApproximateNumberOfMessagesVisible` ≥ 1、期間 300 秒） | Invoke 失敗・非同期失敗の検知 |
| アラームの通知先 | 上記 4 本すべての `AlarmActions` を **core の Ops トピック**（SNS `example-backoffice-ops-{env}`）に接続する。ARN はスタックパラメータ `OpsTopicArn`（SSM 型 `/example-backoffice/{env}/ops/topic-arn`）で受ける | **トピックのメール購読は手で作る**（CloudFormation のインライン購読は確認期限切れで消える。プラットフォーム設計 13.4 の手順 6）。購読が Confirmed でなければアラームは鳴っても誰にも届かない |

- 期間を 1 日から 300 秒に縮めたのは、`ExecutionsFailed` の 1 日集計だと翌日まで気づけないため。`EvaluationPeriods: 1` / `TreatMissingData: notBreaching` により、イベントが無い期間はアラームにならない。
- 疎通は `aws cloudwatch set-alarm-state --alarm-name {アラーム名} --state-value ALARM --state-reason smoke` で確認できる（状態は次の評価で自動復帰する）。
| 実行実績 | Powertools Metrics（名前空間 `example-backoffice/saas-a-billing`）: `RunDuration`, `TenantDuration`, `TenantCount`, `TenantFailed`, `ExportCalls`, `ExportBytes`, `MissingDevices`, `TotalAmount` | 毎回記録。`TenantDuration` が 600 秒を超えたら 6.10 の拡張を検討 |
| 未実行の検知 | `trigger` Lambda の判定で兼ねる（当月の RUN が成功するまで毎日 06:00 に再評価し、`auto_retry_days` 到達で「要手動」通知） | 専用のアラームは作らない |
| ログ | Powertools Logger（JSON）。保持 365 日 | `month`, `attempt`, `tenant_cd`, `window`, `status_code`, `bytes` を構造化キーで出す |
| notify の配信結果 | core の notifier 側（DLQ・バウンス） | アプリ側は `notify_message_id` で突合 |

### 14.3 失敗時の再実行手順

1. Slack / メールの失敗通知、または画面の実行履歴で状態と `error` を確認する
2. 原因別の対処: 401 → 専用 API ユーザーの競合（他所でのログイン）を解消する。SSM 取得失敗 → パラメータ名・KMS 権限を確認する。LimitOver が地点単位でも解消しない → `EXPORT_WINDOW_DAYS` を小さくして再デプロイ。1 テナントの時間予算超過 → 6.10 の拡張。ステートマシンの実行履歴（`execution_arn`）で失敗したステートを特定する
3. 画面の手動実行から対象月を指定して実行する（失敗後は `force` 不要）。配布前に確認したい場合は `distribute:false` → 確認 → `distribute:true, force:true`
4. 翌日 06:00 まで放置すれば自動再実行も走る（自動実行が有効で `auto_retry_days` に達していない場合）。自動配布を止めたいときは設定画面で自動実行を無効にする

### 14.4 定常運用

| 作業 | 手順 |
|---|---|
| 本番 API 公開後の切替 | 集計対象環境はサイト固定（3.7）なので**画面の操作は無い**。(1) 本番の専用 API ユーザーを作成する、(2) prod の `/example-backoffice/prod/apps/saas-a-billing/targets/production/{base-url,user-id,password}` を `aws ssm put-parameter --overwrite` で本番の値（base-url `https://saas-a.example.jp`）に差し替える、(3) Lambda のキャッシュ（`max_age=300`）が切れるのを待つか 5 分待つ、(4) `distribute:false` で当月を手動実行し、処理仕様ブロックの「集計対象」のホスト名が `saas-a.example.jp` になっていることと件名に `[検証環境]` が付かないことを確認する、(5) 自動実行を有効にする |
| 単価改定 | 設定画面で新しい `effective_from` の版を追加する（改定通知を受けた時点で先日付登録可）。過去月は影響を受けない |
| 締め日・締め時刻の変更 | 設定画面で `cutoff_day` / `cutoff_time` を変更。翌日の判定から反映 |
| 宛先・Slack チャンネルの変更 | 設定画面。次回実行から反映。Webhook フォールバック中はチャンネル ID の変更が効かない |
| 除外地点の追加 | SaaS_A 側の説明欄・備考欄に除外キーワードを入れる（アプリ側の設定変更は不要） |
| 専用 API ユーザーのパスワード変更 | SaaS_A で変更 → 固定環境の SSM `targets/{TARGET_ENV}/password` を更新（`aws ssm put-parameter --overwrite`）。Lambda は次回実行時に新しい値を取得する（`max_age=300`） |
| 手動実行した人を調べる | 実行履歴 → 該当 RUN の `request_history`（直近 20 件。誰がいつ・配布あり/なし・強制かを記録。7.1） |
| 過去月の参照 | 画面の実行履歴から。S3 のオブジェクトは削除しない |

## 15. 要確認事項

1. 本番 API（Bearer 版）のリリース日と、本番での専用 API ユーザー作成者・権限レベル（export は「システム管理者・テナント管理者・現場管理者」。全テナント横断にはシステム管理者が必要）
2. export の 100,000 行上限が本番環境でも同値か（R4 は検証環境の値）
3. export の「データなし」応答: OpenAPI は 200 + JSON `Error.Export.NoRecords`、R4 実測は 200 + 0 バイト（本設計は両方を受け付ける）
4. `device/list` の `page=0`（全件取得）の上限有無（本設計は `perPage=999` のページ方式）
5. 本番 API の base-url 差し替え時期（14.4）。公開までは prod の `targets/production/base-url` に検証環境の URL が入っているため、prod サイトの「本番」バッジと実際の接続先が一致しない期間がある
6. 中間ファイル 90 日削除（6.7）の実効性: `lifecycle=expire-90` タグは書き手が付ける。ライフサイクルの実削除は運用開始 90 日後まで観測できないため、まずは運用開始直後に `aws s3api get-object-tagging` で `parts/` の 1 件を抜き取り確認する

## 付録 A. 出典

### 社内資料
- 現行の手動集計スキル: saas-a-document リポジトリ `.claude/skills/saas-a-billing-report/scripts/aggregate_report.py`
- SaaS_A 管理用 REST API OpenAPI（2026-08-19 最終版）: saas-a-document リポジトリ `vendor-docs/saas-a-api/openapi.yaml`
- 単価表: support-tracker #2001（2025/9/1 時点）
- 調査レポート R1（基盤構成）、R3（Excel/SES/Slack/スケジューラ/DynamoDB/外部 API）、R4（export 実測、2026-08-27 検証環境）
- プラットフォーム設計書: `Documents/DESIGN.md`

### AWS 公式
- EventBridge Scheduler スケジュール種別・cron・タイムゾーン: https://docs.aws.amazon.com/scheduler/latest/UserGuide/schedule-types.html
- EventBridge Scheduler DLQ: https://docs.aws.amazon.com/scheduler/latest/UserGuide/configuring-schedule-dlq.html
- EventBridge Scheduler RetryPolicy: https://docs.aws.amazon.com/scheduler/latest/APIReference/API_RetryPolicy.html
- Lambda を Scheduler で呼ぶ（非同期 Invoke）: https://docs.aws.amazon.com/lambda/latest/dg/with-eventbridge-scheduler.html
- SAM ScheduleV2: https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/sam-property-function-schedulev2.html
- SAM `AWS::Serverless::StateMachine`: https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/sam-resource-statemachine.html
- Step Functions Map ステート: https://docs.aws.amazon.com/step-functions/latest/dg/state-map.html
- Step Functions のエラー処理（Retry / Catch）: https://docs.aws.amazon.com/step-functions/latest/dg/concepts-error-handling.html
- Lambda タイムアウト: https://docs.aws.amazon.com/en_us/lambda/latest/dg/configuration-timeout.html
- Lambda Python ランタイム: https://docs.aws.amazon.com/lambda/latest/dg/lambda-python.html
- DynamoDB Query（ScanIndexForward）: https://docs.aws.amazon.com/amazondynamodb/latest/APIReference/API_Query.html
- DynamoDB KeyConditionExpression: https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/Query.KeyConditionExpressions.html
- Powertools Parameters: https://docs.aws.amazon.com/powertools/python/latest/utilities/parameters/
- Powertools Event Handler（strip_prefixes）: https://docs.aws.amazon.com/powertools/python/latest/core/event_handler/api_gateway/
- Parameter Store SecureString と KMS: https://docs.aws.amazon.com/kms/latest/developerguide/services-parameter-store.html
- Lambda 外部パラメータ取得方式の比較: https://aws.amazon.com/blogs/compute/choosing-the-right-solution-for-aws-lambda-external-parameters/
- S3 署名付き URL の有効期限（一時クレデンシャル）: https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-presigned-url.html
- SES 添付（v2 Attachments、40MB、禁止拡張子）: https://docs.aws.amazon.com/ses/latest/dg/attachments.html
- SQS メッセージ上限 1 MiB: https://aws.amazon.com/about-aws/whats-new/2025/08/amazon-sqs-max-payload-size-1mib
- HTTP API JWT authorizer: https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-jwt-authorizer.html
- CloudFormation SSM パラメータ型: https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/cloudformation-supplied-parameter-types.html
- aws-samples Scheduler → Lambda DLQ パターン: https://github.com/aws-samples/serverless-patterns/tree/main/eventbridge-schedule-to-lambda-dlq-sam-python

### ライブラリ
- XlsxWriter: https://pypi.org/project/XlsxWriter/
- XlsxWriter vs openpyxl: https://xlsxwriter.com/xlsxwriter-vs-openpyxl/
- openpyxl 日本語列幅（east_asian_width）: https://qiita.com/Nomisugi/items/f451ad9f67d3e419c73b
- responses（HTTP モック）: https://oneuptime.com/blog/post/2025-01-06-python-mock-external-apis/view
- moto で Lambda 単体テスト: https://seifrajhi.github.io/blog/unit-testing-aws-lambda-moto/
