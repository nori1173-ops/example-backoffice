# SaaS_A 月額課金レポート アプリ（saas-a-billing）実装計画（dev-tracker #1003）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** SaaS_A の全テナント・全地点を毎月の締め日で棚卸しし、ベンダーAからの月額ライセンス請求書と照合するための Excel を自動生成・自動配布する。dev 環境で検証環境（`verification`）を集計し、Excel をメールで受領できる状態まで作る。

**Architecture:** EventBridge Scheduler（毎日 06:00 JST）→ `trigger` Lambda が締め日時と RUN 状態を判定 → Step Functions（Standard）`saas-a-billing-run` が `Init`（設定・単価解決・getToken・tenant/list）→ `CollectTenants`（Map、`MaxConcurrency: 2`。テナントごとに device/list・group/list・export を 7 日窓で取得し S3 parts に保存）→ `Aggregate`（parts 集約・判定・削除地点差分・Excel 生成・S3 保存・共通 notify へ投函）。API は HTTP API + Cognito JWT authorizer で SPA から実行履歴・内訳・手動実行・設定を提供する。

**Tech Stack:** AWS SAM、Lambda（Python 3.13）、Step Functions（Standard）、DynamoDB 単一テーブル、S3（core の添付バケット）、EventBridge Scheduler、API Gateway HTTP API、XlsxWriter、requests、Powertools for AWS Lambda (Python)、shared レイヤ `example_backoffice`、Vue 3 / Vuetify 4 / Pinia 4、pytest / moto / responses / openpyxl、Vitest。

**Spec:**
- `Documents/apps/saas-a-billing/DESIGN.md`（**本計画の正本**）
- `Documents/DESIGN.md`（プラットフォーム設計書。SSM 契約・shared レイヤ・通知・命名・タグの正本）
- `Documents/ADD-APP-GUIDE.md`（アプリ追加の 10 ステップとレビューチェックリスト）

**前提計画:** `Documents/plans/2026-08-28-platform-plan.md`（dev-tracker #1002）。着手できる時期はフェーズごとに違う。

- **Phase A〜D と E-1 / E-2** — 基盤 **Phase 1（shared レイヤ）完了後**に着手できる（`auth` / `response` / `origin_verify` / `logging` / `notify` を使う）。
- **E-3（SAM テンプレート / edge-snippet）** — 基盤 **P3-3（edge テンプレート）と P4-6（`templates/app` 雛形）に依存**するため**基盤 Phase 3・4 完了後**。`edge-snippet.yaml` は P3-3 の origin / behavior と一致させ、`template.yaml` は P4-6 の雛形に合わせて書く。
- **F-1〜F-4（フロントエンド）** — 基盤 **P4-4（`createAppApi` / ルーター / ガード）と P4-6（`frontend/src/apps/_template` と E2E 土台）に依存**するため**基盤 Phase 4 完了後**。
- **Phase G（dev デプロイ・実運用確認）** — 基盤 **Phase 5（dev デプロイ）完了が必須**。

---

## Global Constraints

**プラットフォーム計画の Global Constraints をすべて継承する**（AWS 環境・共有リソース保護・ローカルツールチェーン・ファイル規約 CRLF/LF・コミット規約 `mod refs #1001`・検証コマンド）。以下はこのアプリに固有の追加制約。

### 業務ルール（設計 2 章。**変更禁止**）

- 締め日時 = 対象月の `cutoff_day` 日 `cutoff_time`（既定 20 日 `24:00`）。`24:00` は翌日 00:00 の意味。
- 課金対象期間 = `[前月の締め日時, 当月の締め日時)` の**半開区間**（既定 前月 21 日 00:00:00 〜 当月 20 日 23:59:59。受信日時 `change_datetime` 基準、JST）。
- 課金判定の優先順位は **除外 > 通信なし > 課金**。除外は**テナント説明欄（`tenant_description`）と地点備考欄（`device_description`）だけ**を見る（地点名・地点CD・現場名は判定に使わない）。
- 通信なしは **0 円**（現行スキル `aggregate_report.py` は課金加算していた。**移植時の変更点**）。
- 単価表は DynamoDB の版から引く（現行スキルはハードコード。**移植時の変更点**）。締め日時点で有効な版を使う。
- 1,700 超は上限帯の単価を仮適用して課金し `OVER_MAX_ITEMS`、`retention_period` が 1/3 以外は 3 年扱いの仮単価で `RETENTION_UNKNOWN`、項目数 0 は 0 円で `NO_ITEMS`。いずれも赤字「要確認」。
- 「境界日のみ受信」は判定・金額を変えない。備考に印を付けるだけ。

### 移植元（設計 6.5 の移植点）

`~/notes/saas-a-document/.claude/skills/saas-a-billing-report/scripts/aggregate_report.py` の `calc_fee()`（35-46 行）。**帯の走査ロジックだけを参考にし、以下 3 点は設計 2 章に従って変える。**
1. 除外判定の対象を説明欄・備考欄に限定する（現行は地点名・地点CD も対象）。
2. 通信なし地点は 0 円（現行は課金加算）。
3. 単価表を DynamoDB の版から受け取る（現行はモジュール定数 `PRICE_3Y` / `PRICE_1Y` / `CAMERA_PRICE`）。

### SaaS_A API（設計 4 章 + OpenAPI 実測）

正本: `~/notes/saas-a-document/vendor-docs/saas-a-api/openapi.yaml`

| 用途 | エンドポイント | 応答の形 |
| --- | --- | --- |
| 認証 | `POST /api/v2/getToken` body `{user_cd, passwd}` | `{"userInfo": {...}, "token_info": {"token_type":"Bearer", "access_token": "...", "refresh_token": "...", "expires_at": "2026-06-15 08:48:25"}}` |
| テナント一覧 | `GET /api/v2/tenant/list?page=&perPage=` | `{"data": {"count": N, "rows": [{tenant_id, tenant_cd, tenant_name, tenant_description, retention_period, device_count, ...}]}}` |
| 現場一覧 | `GET /api/v2/group/list?tenant_id=` | `{"groups": [{id, name, group_cd, ...}]}` |
| 地点一覧 | `GET /api/v2/device/list?tenant_id=&page=&perPage=` | `{"data": {"count": N, "rows": [{device_id, device_cd, device_name, format_name, format_id, device_description, group_cds: [], group_names: [], format_detail: [...], ...}]}}` |
| 受信データ | `GET /api/v2/download/export?tenant_id=&start_date=&end_date=&group_cd=all&device_cd=all&push_type=csv&output_type=raw_data` | 200 + CSV（**Shift-JIS / cp932**）、200 + JSON `{"errorStatus":"Error.Export.NoRecords","message":""}`、200 + 0 バイト、400 + JSON `{"errorStatus":"Bad Request","message":"Error.Export.LimitOver,123456,100000"}` |

- 認証は `Authorization: Bearer <access_token>`。`start_date` / `end_date` は `YYYY-MM-DD`（両端を含む）。`end_date` が未来日なら API 側が現在時刻に丸める。
- CSV はフォーマットごとのブロックに分かれ、**ブロック先頭にヘッダ行 `field_name,point_cd,point_name,change_datetime,パケット種別,...` が再出現する**。カメラブロックのヘッダは `field_name,point_cd,point_name,change_datetime,パケット種別,ファイル名,Base64形式データ`（Base64 列の値は `"SUCCESS"` 固定）。
- `field_name`（現場名）は複数現場所属だと `"現場A,現場B"` のように引用符付きカンマ結合で来る。**`split(",")` を使わず `csv.reader` に任せる。**
- `point_cd`（2 列目）は `device/list` の `device_cd` と一致する。

### 秘密情報

- SaaS_A の接続情報は SSM SecureString のみ: `/example-backoffice/{env}/apps/saas-a-billing/targets/{target_env}/{base-url,user-id,password}`（`target_env` ∈ `verification` / `production`）。
- **トークン・パスワード・`Authorization` ヘッダをログに出さない。** `requests` のデバッグログを有効にしない。ローカル変数で保持し、モジュールグローバルに置かない。
- 設計書・README・チケット・テストフィクスチャに実際の認証情報を書かない。

### タグ（設計 14.2）

- `apps/saas-a-billing/samconfig.toml` の `tags` = `Project=example-backoffice App=saas-a-billing Env={env}`。
- テンプレート内のタグ対応リソース全部に `Project`（`!Ref ProjectName`）+ `Name`（`Function` / `Api` / `Table` / `LogGroup` / `Role` / `StateMachine` / `Schedule` / `Queue`）。
- `tests/infra/test_template_tags.py`（プラットフォーム計画 P0-4）が `apps/*/template.yaml` を自動で拾うため、**このテストを通すことがテンプレートタスクの受け入れ条件**になる。

### テストフィクスチャ

- CSV フィクスチャは R4 の実測データ（`tenant_all_0814_0820.csv`）から各ブロック先頭 2〜3 行を抜粋して作る。**地点CD・現場名の実データを含むため社外に出さない。** フィクスチャは `apps/saas-a-billing/tests/fixtures/` に置き、**cp932 で保存する**。`.gitattributes` の先頭行が `* -text`（プラットフォーム計画 Task P0-1）なので改行変換は行われず、cp932 のバイト列がそのまま保存される。**`.gitattributes` への追加設定は不要**だが、作成後に `file` と `python -c` での読み込みで CRLF 混入・文字化けが無いことを確認する。
- 実データが手元に無い場合は、**設計 2.4 / 6.4 の仕様どおりの構造を持つ合成データ**を作る（地点CD・現場名は架空の値にする）。その場合はその旨を報告する。

---

## 前提と判断

設計書に書かれていない判断は以下のとおり決めた。業務ルール・データ設計は変更していない。

1. **実装順は「純粋関数 → 外部 I/O → Excel → ハンドラ → IaC → フロント → デプロイ」。** 設計 13 章が「TDD: 純粋関数（period / pricing / billing / export_parser）のテストを先に書く」としているのに従う。
2. **`saas_a_billing` パッケージは `apps/saas-a-billing/functions/saas_a_billing/` に置き、pytest からは `pythonpath` で import する。** ルートの `pyproject.toml` の `[tool.pytest.ini_options].pythonpath` に `apps/saas-a-billing/functions` を追加する（Task A-1）。
3. **時刻はすべて JST の aware `datetime` で扱う。** `zoneinfo` ではなく `datetime.timezone(timedelta(hours=9))` を定数 `JST` として使う（Lambda に tzdata が無い可能性を避ける）。Excel に書くときだけ naive に落とす（XlsxWriter は aware を受け付けない）。
4. **`export` の CSV パースは `resp.iter_lines()` のバイト列を `cp932` でデコードし、`csv.reader` に generator を渡す。** 行を配列に貯めない（設計 6.4）。
5. **`repo.py` は boto3 の `resource("dynamodb").Table(...)` ではなく `client("dynamodb")` を使い、型付きの属性値を明示的に組む。** `Decimal` の混入と `float` 変換の事故を避ける。読み出し時に `Decimal` → `int` へ変換するヘルパを持つ。
6. **`excel.py` のテストは `openpyxl` で読み戻して検証する**（設計 13 章）。XlsxWriter は書き込み専用のため。`openpyxl` は dev 依存にのみ入れ、Lambda の `requirements.txt` には入れない。
7. **Step Functions のローカル検証は Step Functions Local を使わず、`run.asl.json` の JSON 構造を pytest で検査する**（状態名・`Type`・`Next`・`Retry` / `Catch` / `MaxConcurrency` / `ItemSelector` の有無）。加えて `sam validate --lint` で SAM 側の整合を見る。Step Functions Local を動かせた場合は追加で実行してよい。
8. **`api` Lambda の handler テストは Powertools の `APIGatewayHttpResolver` にイベント dict を直接渡す**（`moto` で DynamoDB / S3 / SSM / Step Functions をモックする）。`requestContext.authorizer.jwt.claims` を注入するファクトリ `make_api_event()` を conftest に置く（流用元: `/mnt/c/Users/devA/git/ref-project-a/tests/integration/api/conftest.py` の `make_api_event`）。
9. **`GET /settings` の seed は `version = 0` で DynamoDB に書き込む**（設計 7.1）。読み取り専用ユーザーでも `PutItem` が要るため、api ロールに `dynamodb:PutItem` を付ける（設計 12.2 に既にある）。
10. **フロントの画面は最小限の機能実装にとどめ、デザインの作り込みはしない**（設計 9 章「UI デザインは後で詰める」）。Vuetify の標準コンポーネントと設計 6.6 の色定義をそのまま使う。
11. **`POST /runs` は 202 を返し、SPA はポーリングで状態を追う**（設計 5.3 の 30 秒制約）。SPA のポーリング間隔は 5 秒、上限 10 分とする。
12. **dev で自動実行は無効のまま運用する**（設計 3.3 の既定「自動実行: 無効」）。`auto_run_enabled: false` を seed の既定値にする。
13. **`GET /audits`（`admin` のみ、直近 50 件）を API に追加する。** 設定画面の変更履歴（設計 9 章）を出すために必要だが、**設計 8 章のエンドポイント表には無い追加**。`repo.list_audits()` をそのまま公開する。**設計書へは後日反映する。**
14. **`execution_arn` はステートマシンから受け取って `Init` が RUN に書く。** ASL の `Init` の `Parameters` に `"execution_arn.$": "$$.Execution.Id"` を入れ、`repo.set_execution_arn()`（条件 `attempt = :attempt`）で保存する（障害追跡。設計 14.3）。Lambda 側から `StartExecution` の戻り値を書きに行かない。
15. **RUN に `retry_alert_sent`（bool、既定 `false`）属性を追加する。** 自動再実行の上限到達アラーム（設計 11.1）を 1 回だけ送るためのフラグ。`repo.mark_retry_alert_sent()` で立てる。**設計 7.1 へは後日反映する。**
16. **`target_month` は `(cutoff_day, cutoff_time)` から「締め日が属する年月」を返す。** 締め日時から 1 秒引いた日付で代用すると `cutoff_time="00:00"` のときに月がずれるため。`ClosingPeriod` は半開区間の終端を `end_exclusive`、締め日を `cutoff_date` として別々に持つ。
17. **Slack のメンションは `notify.Slack(mention=...)` にだけ渡す。** `messages.slack_text()` は本文だけを組み立て、メンション文字列を含めない（二重メンション防止）。
18. **Excel の添付は `notify.put_attachment()` を使わず `notify.Attachment(key=storage.xlsx_key(...), filename=...)` で既存の S3 キーを参照する。** Excel は `Aggregate` が既に添付バケットへ置いており、`put_attachment()` を呼ぶと同じファイルを二重保存するため。`storage` のキー規約 `{S3_PREFIX}/{target_env}/{YYYY}/{MM}/{filename}` は shared の `attachment_key(app, filename, subprefix=target_env)` と同じ形にそろえる。

### 要確認（実装中に判明したら報告する。勝手に設計を変えない）

| # | 事項 | 対応 |
| --- | --- | --- |
| 1 | R4 の実測 CSV（`tenant_all_0814_0820.csv`）が手元にあるか。無ければ合成フィクスチャで代替してよいか | Task A-1 で確認して報告する |
| 2 | 本番 API（Bearer 版）のリリース日（設計 15 の #1）。本計画では `target_env: verification` のみで完結させる | Phase G の範囲外 |
| 3 | export の 100,000 行上限が本番環境でも同値か（設計 15 の #2） | 本計画では検証環境のみ。実装は `Error.Export.LimitOver,<件数>,<上限>` の**上限値をメッセージから読む**ようにして値をハードコードしない |
| 4 | `device/list` の `perPage` 上限（設計 15 の #4）。本計画は `perPage=999` のページ方式 | Task B-1 の実 API 疎通（Phase G）で確認 |
| 5 | Excel の「テーブル」（`add_table`）とサマリブロックの共存でレイアウトが崩れないか | Task C-2 で openpyxl 読み戻しと、Phase G の目視確認 |
| 6 | `slack_channel_id` が未設定のとき `channels` から `slack` を外す仕様（設計 6.8）と、notifier 側の「bot/webhook 両方未登録は恒久エラー」（プラットフォーム計画の前提と判断 7）の関係 | アプリ側で外すので通常は衝突しない。Phase G で実測して報告する |

### ユーザー作業チェックポイント（サブエージェントは実施できない）

| # | いつ | 作業 |
| --- | --- | --- |
| CP-B1 | Phase G の前 | **SaaS_A 検証環境に専用 API ユーザーを作成する**（MFA なしの連携用、システム管理者権限。`saasa-dev` MCP のユーザーとは別）。設計 14.1 の #1 |
| CP-B2 | Phase G の前 | **SSM に検証環境の接続情報を登録する**: `/example-backoffice/dev/apps/saas-a-billing/targets/verification/base-url`（`https://verify.saas-a.example.net`）、`user-id`、`password` を SecureString で。設計 14.1 の #2。**値をリポジトリに残さない** |
| CP-B3 | Task G-2 | **`#proj-saas-a-billing` のチャンネル ID**（`C...`）を Slack から取得して伝える。設計 14.1 の #3 |
| CP-B4 | Task G-4 | メール受信と Slack 投稿の目視確認。**宛先は `devA@example.co.jp` のみ、Slack はテスト用または CP-B3 のチャンネルのみ** |

---

## File Structure

```text
apps/saas-a-billing/
├── template.yaml                 HttpApi / 6 Lambda / StateMachine / Table / ScheduleV2 / DLQ / SSM
├── samconfig.toml                [default]=dev, [prod]
├── edge-snippet.yaml             edge に転記する parameter + origin + behavior
├── README.md                     ローカル実行・デプロイ手順
├── statemachine/run.asl.json     Init → CollectTenants(Map) → Aggregate、全体 Catch → MarkFailed
├── functions/                    全関数が共有する 1 パッケージ（CodeUri は 1 つ、Handler だけ分ける）
│   ├── requirements.txt          XlsxWriter, requests
│   ├── api/app.py                APIGatewayHttpResolver。ルート定義のみ
│   ├── batch/handlers/{trigger,init,collect_tenant,aggregate,mark_failed}.py
│   └── saas_a_billing/
│       ├── __init__.py
│       ├── period.py             締め日時・課金対象期間・対象月・窓分割（純粋関数）
│       ├── pricing.py            単価表の版解決・単価引き（純粋関数）
│       ├── billing.py            除外/通信なし/課金の判定・金額計算（純粋関数）
│       ├── export_parser.py      CSV 行ストリーム → 受信集合・カメラ集合・初回/最終受信日時
│       ├── messages.py           メール件名・本文・Slack 文面の組立（純粋関数）
│       ├── saasa_client.py     getToken / list / export（requests、ストリーム）
│       ├── repo.py               DynamoDB（SETTINGS / PRICE / RUN / AUDIT）
│       ├── storage.py            S3 put/get/presign（parts / xlsx / result.json）
│       └── excel.py              XlsxWriter で 2 シートを生成（BytesIO）
└── tests/
    ├── conftest.py               moto fixture、make_api_event、設定/単価/地点のファクトリ
    ├── unit/                     period / pricing / billing / export_parser / messages / excel
    ├── integration/              moto + responses で handler を一気通貫
    └── fixtures/                 縮小 CSV（cp932）、API 応答 JSON

frontend/src/apps/saas-a-billing/
├── index.ts                      AppManifest
├── routes.ts                     /saas-a-billing 配下の子ルート
├── api.ts                        createAppApi('saas-a-billing') とエンドポイント関数
├── types.ts                      Settings / PriceTable / Run / RunDetail の型
├── stores/{runs.ts,settings.ts}
├── views/{RunListView,RunDetailView,SettingsView}.vue
├── components/{RunDialog,EnvBadge,JudgementChip,PriceTableEditor}.vue
└── __tests__/

tests/infra/test_saas_a_billing_template.py   テンプレートとステートマシンの静的検査
```

---

## Phase A: 純粋関数（設計 6.5 / 2 章）

### Task A-1: パッケージ骨格とテスト土台

**目的:** `apps/saas-a-billing/` の骨格を作り、pytest が import できる状態にする。フィクスチャの入手可否を確定する。

**Files:**
- Create: `apps/saas-a-billing/functions/saas_a_billing/__init__.py`, `apps/saas-a-billing/functions/api/__init__.py`, `apps/saas-a-billing/functions/batch/__init__.py`, `apps/saas-a-billing/functions/batch/handlers/__init__.py`, `apps/saas-a-billing/functions/requirements.txt` — **`pythonpath` 経由で import・カバレッジ計測する 3 パッケージ（`saas_a_billing` / `api` / `batch`）すべてに `__init__.py` を置く**
- Create: `apps/saas-a-billing/tests/__init__.py`, `tests/unit/__init__.py`, `tests/integration/__init__.py`, `tests/conftest.py`
- Create: `apps/saas-a-billing/tests/fixtures/`（CSV / JSON）
- Modify: `pyproject.toml`（`[tool.pytest.ini_options].pythonpath` に `apps/saas-a-billing/functions` を追加する。**`testpaths` は基盤計画 Task P0-2 で既に `apps` を含むため追加しない**）

**流用元:** `/mnt/c/Users/devA/git/ref-project-a/tests/integration/api/conftest.py` — ダミー AWS 認証情報の `autouse` fixture、`with mock_aws():` でテーブルを作る fixture、`make_api_event()`。

**公開インターフェース（`tests/conftest.py`）:**
- autouse fixture `_aws_credentials` — `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_DEFAULT_REGION=ap-northeast-1` / `ENV=dev` / `APP_NAME=saas-a-billing` / `S3_PREFIX=saas-a-billing` / `SSM_PREFIX=/example-backoffice/dev/apps/saas-a-billing` / `PORTAL_URL=https://backoffice-dev.example-cloud.com` / `API_PREFIX=/api/saas-a-billing` / `EXPORT_WINDOW_DAYS=7` / `TIME_BUDGET_MARGIN_SEC=150`（**リソース依存の `TABLE_NAME` / `ATTACHMENTS_BUCKET` / `NOTIFY_QUEUE_URL` / `STATE_MACHINE_ARN` は各リソース fixture が設定する**。設計 10 章の環境変数表と対応させる）。
- fixture `dynamodb_table` — `moto` で `example-backoffice-saas-a-billing-data-dev`（PK/SK、`PAY_PER_REQUEST`）を作り、`TABLE_NAME` を設定する。
- fixture `s3_bucket` — 添付バケットを作り `ATTACHMENTS_BUCKET` を設定する。
- fixture `notify_queue` — SQS キューを作り `NOTIFY_QUEUE_URL` を設定する。
- fixture `state_machine` — `moto` の Step Functions にダミーのステートマシンを作り `STATE_MACHINE_ARN` を設定する（D-1 / E-2 の `StartExecution` テストが使う）。
- fixture `ssm_targets` — `/example-backoffice/dev/apps/saas-a-billing/targets/verification/{base-url,user-id,password}` を SecureString で登録する（**ダミー値**）。あわせて `/example-backoffice/dev/shared/slack/mode`（**String**、値 `webhook`）も登録する（`GET /settings` が読むため。基盤計画 P1-5 の SSM 契約）。
- `def make_api_event(method, path, body=None, query=None, groups=None, email="devA@example.co.jp", sub="test-sub") -> dict` — HTTP API v2.0 のイベント。`requestContext.authorizer.jwt.claims` に `sub` / `email` / `cognito:groups` を入れる。`headers` に `x-origin-verify` を入れる。
- ファクトリ: `def make_settings(**overrides) -> dict`（設計 3.3 の既定値）、`def make_price_table(**overrides) -> dict`（設計 2.3 の #2001 の値）、`def make_device(**overrides) -> dict`、`def make_tenant(**overrides) -> dict`。

**フィクスチャ（`tests/fixtures/`）:**
- `export_multi_block.csv`（cp932）— 通常ブロック 2 種 + カメラブロック 1 種。引用符付き現場名（`"現場A,現場B"`）と引用符内カンマを含む。各ブロック 2〜3 行。
- `export_empty.csv` — 0 バイト。
- `export_no_records.json` — `{"errorStatus":"Error.Export.NoRecords","message":""}`。
- `export_limit_over.json` — `{"errorStatus":"Bad Request","message":"Error.Export.LimitOver,123456,100000"}`。
- `get_token.json` / `tenant_list.json` / `device_list.json` / `group_list.json` — 上記「SaaS_A API」の応答形に沿った縮小版（**架空の値**）。

**手順:**
- [ ] **要確認 #1:** R4 の実測 CSV が手元にあるか確認する（`find /mnt/c/Users/devA -name 'tenant_all_*.csv' 2>/dev/null`）。無ければ設計 2.4 / 6.4 の仕様どおりの**合成データ**を作り、その旨を報告する。
- [ ] 骨格と conftest、フィクスチャを作る。
- [ ] `pyproject.toml` を更新する（`.gitattributes` は触らない。先頭行の `* -text` で足りる）。
- [ ] `uv run pytest apps/saas-a-billing/tests -v` が「テストが 0 件」で終了コード 5 になることを確認する（またはダミーの `test_conftest_loads` を 1 件だけ置いて passed にする）。
- [ ] `python3 -c "print(open('apps/saas-a-billing/tests/fixtures/export_multi_block.csv', encoding='cp932').read()[:200])"` で cp932 として読めることを確認する。
- [ ] CRLF 変換（**`.csv` は cp932 のため触らない。`file` で確認するだけ**）→ `ruff check apps/` → コミット（`mod refs #1001 saas-a-billing のパッケージ骨格とテスト土台を追加`）。

**受け入れ条件:** conftest が import でき、fixture が使える。CSV フィクスチャが cp932 で読める。要確認 #1 の結果が報告されている。

---

### Task A-2: `period.py`（締め日時・課金対象期間・窓分割）

**目的:** 設計 2.1 / 6.4 の期間計算を純粋関数で実装する。

**Files:** Create `functions/saas_a_billing/period.py`, `tests/unit/test_period.py`

**Spec:** 設計 2.1（締め日時と課金対象期間）、6.2（`latest_cutoff` / `target_month`）、6.4（7 日窓、半分割）、7.3（`closing_period` の `from` / `to` は半開区間の端点）、13 章（テスト観点）。

**公開インターフェース:**
- 定数 `JST = timezone(timedelta(hours=9))`、`EXPORT_WINDOW_DAYS_DEFAULT = 7`
- `def cutoff_at(year: int, month: int, cutoff_day: int, cutoff_time: str) -> datetime` — `cutoff_time == "24:00"` なら「その日の終わり」= 翌日 00:00（JST aware）。`HH:MM` 形式のみ受理し、それ以外は `ValueError`。
- `def latest_cutoff(now: datetime, cutoff_day: int, cutoff_time: str) -> datetime` — `now` 以前で最も新しい締め日時。
- `def cutoff_date(year: int, month: int, cutoff_day: int) -> date` — 締め日そのもの（`cutoff_time` に依らず `cutoff_day` 日）。
- `def target_month(now: datetime, cutoff_day: int, cutoff_time: str) -> str` — `now` 以前で最も新しい締め日時（`latest_cutoff`）の**締め日が属する年月** `YYYY-MM`。**`(cutoff_day, cutoff_time)` から締め日を決めて年月を返す**ので `cutoff_time` が `"24:00"` でも `"00:00"` でも同じ月になる。**「締め日時から 1 秒引いた日付」で代用しない**（`cutoff_time="00:00"` かつ `cutoff_day=1` で前月にずれるため）。
- `@dataclass(frozen=True) class ClosingPeriod: start: datetime; end_exclusive: datetime; cutoff_date: date; cutoff_day: int; cutoff_time: str` — `[start, end_exclusive)` の半開区間。`end_exclusive` は当月の締め日**時**、`cutoff_date` は当月の締め**日**。プロパティ `first_date: date`（`start.date()`）、`last_date: date`（`(end_exclusive - 1秒).date()`。**受信データを取りに行く最終日**で、`cutoff_time="00:00"` のときだけ `cutoff_date` と 1 日ずれる）、`display_end: datetime`（`end_exclusive - 1秒`）、`month: str`（`cutoff_date` の年月）。
- `def closing_period(month: str, cutoff_day: int, cutoff_time: str) -> ClosingPeriod` — 対象月 `YYYY-MM` から `[前月の締め日時, 当月の締め日時)` を作る。`cutoff_date` には当月の締め日（`YYYY-MM` の `cutoff_day` 日）を入れる。**`start` / `end_exclusive` は設計 6.4 の擬似コードの `period.start` / `period.end`、設計 7.3 の `closing_period.from` / `to` と同じもの**（半開区間であることを名前で明示しただけ）。
- `def windows(start_date: date, end_date: date, days: int = 7) -> list[tuple[date, date]]` — 両端を含む日付範囲を `days` 日窓に分割。
- `def split_window(ws: date, we: date) -> tuple[tuple[date, date], tuple[date, date]]` — 窓を半分にする（設計 6.4 の `mid = ws + (days // 2 - 1)`）。1 日窓は `ValueError`。
- `def in_period(ts: str, period: ClosingPeriod) -> bool` — `"YYYY/MM/DD HH:MM:SS"` を JST として `[start, end_exclusive)` に入るか判定。

**テストケース（設計 13 章の観点）:**
- `test_cutoff_at_24_00_is_next_day_midnight` — 2026-10-20 `24:00` → 2026-10-21 00:00+09:00。
- `test_cutoff_at_00_00_is_same_day_midnight` — 2026-10-20 `00:00` → 2026-10-20 00:00+09:00（**翌日にしない**）。
- `test_cutoff_at_arbitrary_time` — `18:30` → 2026-10-20 18:30+09:00。
- `test_cutoff_at_rejects_invalid_time` — `"25:00"` / `"1830"` は `ValueError`。
- `test_cutoff_day_1_to_28`（parametrize）— 各 `cutoff_day` で日付が正しい。
- `test_latest_cutoff_before_cutoff_returns_previous_month` — 10/20 05:00 に判定すると 9/21 00:00（既定設定）。
- `test_latest_cutoff_on_cutoff_day_after_time_returns_this_month` — 10/21 06:00 に判定すると 10/21 00:00。
- `test_latest_cutoff_crosses_year` — 1/5 に判定すると 12/21 00:00。
- `test_target_month_for_24_00` — `now=2026-10-21 06:00`、`cutoff_day=20`、`cutoff_time="24:00"` → `"2026-10"`。
- `test_target_month_for_normal_time` — `now=2026-10-20 19:00`、20、`"18:30"` → `"2026-10"`。
- `test_target_month_for_00_00` — `now=2026-10-01 06:00`、`cutoff_day=1`、`cutoff_time="00:00"` → `"2026-10"`（**締め日時から 1 秒引く実装だと `"2026-09"` になる**）。
- `test_closing_period_default` — `month="2026-10"`, 20, `24:00` → `start`=2026-09-21 00:00、`end_exclusive`=2026-10-21 00:00、`display_end`=2026-10-20 23:59:59、`first_date`=9/21、`last_date`=10/20、`cutoff_date`=10/20。
- `test_closing_period_cutoff_date_equals_last_date_for_24_00` — 既定設定では `cutoff_date == last_date`。
- `test_closing_period_cutoff_date_differs_from_last_date_for_00_00` — `month="2026-10"`, 20, `"00:00"` → `end_exclusive`=2026-10-20 00:00、`display_end`=2026-10-19 23:59:59、`last_date`=10/19、`cutoff_date`=10/20。
- `test_closing_period_crosses_year` — `month="2026-01"` → start=2025-12-21。
- `test_closing_period_start_is_inclusive_end_is_exclusive` — `in_period` で `start` は True、`end_exclusive` は False、`end_exclusive - 1秒` は True。
- `test_windows_splits_31_days_into_5` — 8/21〜9/20 を 7 日窓で → `[(8/21,8/27),(8/28,9/3),(9/4,9/10),(9/11,9/17),(9/18,9/20)]`（設計 6.4 の例と一致）。
- `test_windows_single_day` — 同日は 1 窓。
- `test_windows_exact_multiple` — 14 日を 7 日窓で 2 窓。
- `test_split_window_even_days` — 8 日窓 → 4 日 + 4 日。
- `test_split_window_odd_days` — 7 日窓 → 3 日 + 4 日。
- `test_split_window_single_day_raises`
- `test_in_period_boundary_seconds` — `"2026/09/21 00:00:00"` True、`"2026/09/20 23:59:59"` False、`"2026/10/20 23:59:59"` True、`"2026/10/21 00:00:00"` False。

**手順:** テスト作成 → `uv run pytest apps/saas-a-billing/tests/unit/test_period.py -v` で失敗確認 → 実装 → 全 passed → CRLF 変換・確認 → `ruff check apps/` → コミット（`mod refs #1001 saas-a-billing の period（期間計算）を追加`）。

**受け入れ条件:** 上記 23 件 passed。設計 6.4 の窓分割例と一致。`target_month` が `cutoff_time` の値に依存しない。CRLF。

---

### Task A-3: `pricing.py`（単価表の版解決と単価引き）

**目的:** 設計 2.3 の単価表と 6.5 の `resolve_price` を純粋関数で実装する。

**Files:** Create `functions/saas_a_billing/pricing.py`, `tests/unit/test_pricing.py`

**Spec:** 設計 2.3（単価表）、6.5（`resolve_price`）、7.1（`PRICE` アイテムの構造）、13 章。
**移植元:** `aggregate_report.py` の `calc_fee()`（帯の走査だけ）。**単価表をハードコードしない。**

**公開インターフェース:**
- `@dataclass(frozen=True) class Band: max_items: int; price_3y: int; price_1y: int`
- `@dataclass(frozen=True) class PriceTable: effective_from: str; bands: list[Band]; camera_3y: int; camera_1y: int; note: str = ""` — `bands` は `max_items` 昇順。`from_item(item: dict) -> PriceTable` クラスメソッドで DynamoDB アイテムから作る。
- `def resolve_price(table: PriceTable, retention: int | None, item_count: int | None, is_camera: bool) -> tuple[int, list[str]]` — 戻り値は `(単価, フラグ配列)`。
  - `retention` が 1 なら `price_1y` 列、それ以外（3 / None / 不正値）は `price_3y` 列。`retention not in (1, 3)` なら `RETENTION_UNKNOWN` を付ける。
  - `is_camera` なら該当列のカメラ単価を返す（`item_count` は見ない）。
  - `item_count` が 0 / None なら `(0, [..., "NO_ITEMS"])`。
  - 帯を `max_items` 昇順に走査し、最初に `item_count <= band.max_items` を満たす帯の単価。
  - どの帯にも入らなければ最終帯の単価 + `OVER_MAX_ITEMS`。
- `def select_table(items: list[dict], as_of: date) -> PriceTable` — `effective_from <= as_of` の版のうち最新。該当なしなら `PriceTableNotFoundError`。
- `class PriceTableNotFoundError(Exception)`
- `def default_price_table_item(effective_from: str = "2025-09-01") -> dict` — 設計 2.3 の #2001 の値（seed 用）。

**テストケース（設計 13 章の観点）:**
- `test_band_boundaries`（parametrize）— 500 → 650、501 → 1000、800 → 1000、801 → 1200、1600 → 1200、1601 → 1350、1700 → 1350（3年）。
- `test_band_boundaries_1y`（parametrize）— 500 → 450、501 → 700、1600 → 900、1700 → 1000。
- `test_over_max_items_uses_last_band_and_flags` — 1701 → `(1350, ["OVER_MAX_ITEMS"])`。
- `test_camera_price_3y` → 1800、`test_camera_price_1y` → 1400。
- `test_camera_ignores_item_count` — `item_count=5000` でもカメラ単価。
- `test_zero_items_returns_zero_with_no_items_flag` — `(0, ["NO_ITEMS"])`。
- `test_none_items_returns_zero_with_no_items_flag`
- `test_retention_none_uses_3y_and_flags_unknown` — `(..., ["RETENTION_UNKNOWN"])`。
- `test_retention_5_uses_3y_and_flags_unknown`
- `test_camera_with_unknown_retention_flags_and_uses_3y` — `(1800, ["RETENTION_UNKNOWN"])`。
- `test_zero_items_with_unknown_retention_has_both_flags` — `["RETENTION_UNKNOWN", "NO_ITEMS"]`。
- `test_select_table_picks_latest_effective_on_or_before` — `2025-09-01` と `2026-04-01` があり `as_of=2026-03-31` → `2025-09-01`。
- `test_select_table_same_day_is_included` — `as_of == effective_from` の版が選ばれる。
- `test_select_table_ignores_future_versions` — 未来版は選ばれない。
- `test_select_table_raises_when_none_effective` — 全部未来なら `PriceTableNotFoundError`。
- `test_default_price_table_matches_2001` — 設計 2.3 の表と一致（4 帯 + カメラ）。
- `test_price_table_from_item_converts_decimal_to_int` — DynamoDB の `Decimal` が `int` になる。

**手順:** テスト作成 → 失敗確認 → 実装 → 全 passed → CRLF 変換・確認 → `ruff check apps/` → コミット（`mod refs #1001 saas-a-billing の pricing（単価表）を追加`）。

**受け入れ条件:** 上記 18 件（parametrize 展開で 30 件超）passed。設計 2.3 の表と完全一致。CRLF。

---

### Task A-4: `billing.py`（判定・金額計算・集計）

**目的:** 設計 2.2 / 6.5 の判定ロジックと集計を純粋関数で実装する。

**Files:** Create `functions/saas_a_billing/billing.py`, `tests/unit/test_billing.py`

**Spec:** 設計 2.2（課金判定）、2.4（カメラ判定）、6.5（`classify` / `boundary_only` / `missing_devices`）、7.3（`result.json` の `devices[]` / `summary` の形）。
**移植元の変更点（設計 6.5）:** 除外判定を説明欄・備考欄に限定、通信なしは 0 円、単価表は引数で受け取る。

**公開インターフェース:**
- `@dataclass class DeviceJudgement:` — `result.json` の `devices[]` 1 件分。フィールド: `device_cd`, `device_name`, `group_cds`, `group_names`, `format_name`, `item_count`, `device_description`, `is_camera`, `camera_source`（`"export"` / `"settings"` / `None`）, `retention`（`1` / `3` / `None`）, `received`, `first_received_at`, `last_received_at`, `boundary_only`, `judgement`（`"billable"` / `"no_data"` / `"excluded"` / `"failed"`）, `unit_price`, `amount`, `flags: list[str]`, `note: str`。
- `def contains_any(text: str | None, keywords: list[str]) -> bool` — 大文字小文字を無視した部分一致。`None` / 空は False。
- `def classify(tenant: dict, device: dict, received: set[str], camera_by_export: set[str], first_seen: dict[str, str], last_seen: dict[str, str], settings: dict, table: PriceTable, period: ClosingPeriod) -> DeviceJudgement` — **優先順位は 除外 > 通信なし > 課金**。除外・通信なしは `amount = 0`（単価は表示用に残す）。
- `def boundary_only(first_seen: str | None, last_seen: str | None, period: ClosingPeriod) -> bool` — 受信が期間の初日だけ、または最終日だけに収まっている。
- `def missing_devices(prev_result: dict | None, current_device_cds: set[str]) -> list[dict]` — 前回 RUN の `result.json` にあり今回の `device/list` に無い地点。前回なしなら `[]`。
- `def summarize(tenants: list[dict], missing: list[dict]) -> dict` — 設計 7.3 の `summary`（`tenant_count`, `device_count`, `billable_count`, `no_data_count`, `excluded_count`, `review_count`, `failed_tenant_count`, `missing_count`, `total_amount`）。
- `def sort_devices(devices: list[DeviceJudgement]) -> list[DeviceJudgement]` — 先頭 `group_cd` 昇順 → `device_cd` 昇順（設計 6.5 末尾）。
- `def sort_tenants(tenants: list[dict]) -> list[dict]` — `tenant_cd` 昇順。

**テストケース（設計 13 章の観点）:**
- `test_excluded_beats_no_data` — 除外キーワードあり かつ 受信なし → `excluded`。
- `test_excluded_beats_billable` — 除外キーワードあり かつ 受信あり → `excluded`、`amount == 0`、`unit_price` は表示用に入る。
- `test_no_data_when_not_received` — 受信なし → `no_data`、`amount == 0`、`unit_price` は入る。
- `test_billable_when_received` — 受信あり → `billable`、`amount == unit_price`。
- `test_exclusion_from_tenant_description` — テナント説明欄に「デモ」。
- `test_exclusion_from_device_description` — 地点備考欄に「デモ」。
- `test_exclusion_ignores_device_name` — **地点名に「デモ」があっても除外しない**（移植点 1）。
- `test_exclusion_ignores_device_cd` — **地点CDに「デモ」があっても除外しない**（移植点 1）。
- `test_exclusion_ignores_group_name`
- `test_exclusion_is_case_insensitive_partial_match` — `"DEMO機"` / `"デモ用"` が既定キーワード `["デモ"]` と別キーワード `["demo"]` で当たる。
- `test_exclusion_with_multiple_keywords`
- `test_camera_by_export` — `camera_by_export` に含まれる → `is_camera` True、`camera_source == "export"`。
- `test_camera_by_settings_format_name` — `settings.camera_format_names` に `format_name` が完全一致 → `camera_source == "settings"`。
- `test_camera_or_condition_export_wins` — 両方該当なら `camera_source == "export"`。
- `test_camera_format_name_requires_exact_match` — 部分一致では当たらない。
- `test_boundary_only_first_day` — 受信が期間初日のみ → True。
- `test_boundary_only_last_day` — 期間最終日のみ → True。
- `test_boundary_only_false_when_spans_days` — 初日と最終日の両方 → False。
- `test_boundary_only_false_for_middle_day` — 中日のみ → False。
- `test_boundary_only_does_not_change_judgement_or_amount` — `boundary_only` が True でも `judgement` / `amount` が変わらない。
- `test_missing_devices_without_previous_run_returns_empty`
- `test_missing_devices_detects_removed_device` — 前回にあり今回に無い地点が 1 件返る。
- `test_missing_devices_ignores_new_device`
- `test_missing_devices_amount_is_not_counted` — `summarize` の `total_amount` に影響しない。
- `test_summarize_counts_by_judgement` — 課金 / 通信なし / 除外 / 要確認 / 取得失敗 / 消えた地点。
- `test_summarize_total_amount_excludes_no_data_and_excluded`
- `test_summarize_review_count_counts_flagged_devices` — `flags` が空でない地点の数。
- `test_summarize_failed_tenant_count`
- `test_sort_devices_by_group_then_device_cd`
- `test_sort_devices_with_multiple_groups_uses_first_group_cd`
- `test_sort_tenants_by_tenant_cd`

**手順:** 移植元 `aggregate_report.py` の `calc_fee` / `main` の判定部を読む → テスト作成 → 失敗確認 → 実装 → 全 passed → CRLF 変換・確認 → `ruff check apps/` → コミット（`mod refs #1001 saas-a-billing の billing（判定・集計）を追加`）。

**受け入れ条件:** 上記 31 件 passed。**移植点 3 つ（除外対象の限定・通信なし 0 円・単価表を引数で受け取る）がテストで固定されている。** CRLF。

---

### Task A-5: `export_parser.py`（CSV ストリーム処理）

**目的:** 設計 6.4 の `parse_stream` を実装する。行を保持せず集合と辞書だけを積む。

**Files:** Create `functions/saas_a_billing/export_parser.py`, `tests/unit/test_export_parser.py`

**Spec:** 設計 2.4（カメラ判定）、6.4（CSV ストリーム処理）、13 章。

**公開インターフェース:**
- `@dataclass class ExportResult: received: set[str]; camera: set[str]; first_seen: dict[str, str]; last_seen: dict[str, str]; rows: int; bytes_read: int` — `merge(other)` で複数窓の結果を合成する。
- `def parse_lines(lines: Iterable[str], period: ClosingPeriod, result: ExportResult) -> None` — 既存の `result` に積む（複数窓で使い回す）。
  - 行頭 1 列目が `"field_name"` の行を**ブロック境界**として扱う。その行に `"Base64形式データ"` が含まれればカメラブロック。
  - データ行は 2 列目が `point_cd`、4 列目が `change_datetime`（`"YYYY/MM/DD HH:MM:SS"`。文字列比較で大小比較できる）。
  - `in_period(ts, period)` が False の行はスキップする（境界日の時刻絞り込み）。
  - `first_seen` / `last_seen` を更新し、`received` に `point_cd` を足す。カメラブロックなら `camera` にも足す。
  - 空行はスキップ。`csv.reader` に任せて引用符内のカンマ・改行を正しく扱う。
- `def parse_response(raw_lines: Iterable[bytes], period: ClosingPeriod, result: ExportResult) -> None` — バイト列を `cp932`（`errors="replace"`）でデコードして `parse_lines` に渡す。
- `def is_no_records(body: bytes | str) -> bool` — 0 バイト、または JSON で `errorStatus == "Error.Export.NoRecords"` なら True。
- `def parse_limit_over(message: str) -> tuple[int, int] | None` — `"Error.Export.LimitOver,123456,100000"` → `(123456, 100000)`。該当しなければ `None`。**上限値をハードコードしない**（要確認 #3）。

**テストケース（フィクスチャ `export_multi_block.csv` を使う）:**
- `test_parses_multiple_blocks` — 全ブロックの `point_cd` が `received` に入る。
- `test_header_row_is_not_treated_as_data` — `field_name` 行の 2 列目（`point_cd` という文字列）が `received` に入らない。
- `test_camera_block_detected_by_base64_column` — カメラブロックの地点だけが `camera` に入る。
- `test_non_camera_block_devices_not_in_camera_set`
- `test_quoted_group_name_with_comma_is_handled` — `"現場A,現場B"` の行でも `point_cd` が正しく 2 列目から取れる。
- `test_first_and_last_seen_are_min_and_max` — 同一地点の複数行から最小・最大が取れる。
- `test_rows_outside_period_are_skipped` — 期間外の `change_datetime` の行は `received` に入らない（境界日の時刻絞り込み）。
- `test_boundary_row_at_period_start_is_included` — `2026/09/21 00:00:00`。
- `test_boundary_row_at_period_end_is_included` — `2026/10/20 23:59:59`。
- `test_row_at_exclusive_end_is_excluded` — `2026/10/21 00:00:00`。
- `test_empty_lines_are_skipped`
- `test_merge_combines_two_windows` — 2 窓の結果を合成すると `received` が和集合、`first_seen` が最小、`last_seen` が最大。
- `test_parse_response_decodes_cp932` — 日本語の現場名が化けない。
- `test_parse_response_replaces_undecodable_bytes` — 不正バイトで例外にならない。
- `test_is_no_records_for_empty_body` — `b""` → True。
- `test_is_no_records_for_json` — `Error.Export.NoRecords` → True。
- `test_is_no_records_false_for_csv` — CSV 本体 → False。
- `test_parse_limit_over` — `(123456, 100000)`。
- `test_parse_limit_over_returns_none_for_other_message`
- `test_does_not_accumulate_rows` — 大きめの入力でも `result` に行データを保持していない（`ExportResult` に `rows` カウンタ以外の行データが無いことを構造で保証。属性一覧を assert する）。

**手順:** テスト作成 → 失敗確認 → 実装 → 全 passed → CRLF 変換・確認（**`.csv` フィクスチャは触らない**）→ `ruff check apps/` → コミット（`mod refs #1001 saas-a-billing の export_parser（CSV ストリーム）を追加`）。

**受け入れ条件:** 上記 20 件 passed。cp932 フィクスチャで日本語が化けない。CRLF（CSV を除く）。

---

### Task A-6: `messages.py`（メール件名・本文・Slack 文面）

**目的:** 設計 3.6 / 6.8 の文面を純粋関数で組み立てる。

**Files:** Create `functions/saas_a_billing/messages.py`, `tests/unit/test_messages.py`

**Spec:** 設計 3.6（通知）、6.8（文面表とプレースホルダ）、11.1（失敗・上限到達の文面）。

**公開インターフェース:**
- `def mail_subject(month: str, cutoff_day: int, target_env: str, attempt: int) -> str` — `【SaaS_A】月額ライセンス料金一覧 2026年10月分（10/20締め）`。`target_env == "verification"` なら末尾に ` [検証環境]`。`attempt > 1` なら末尾に `（再送（第 n 回））`。
- `def mail_body(*, month, period: ClosingPeriod, summary: dict, price_effective_from: str, portal_url: str, target_env: str, failed_tenants: list[str]) -> str` — 対象月・課金対象期間・テナント数・地点数・課金・通信なし・除外・要確認・取得失敗・消えた地点・合計金額・使用単価表・ポータル URL。
- `def slack_text(template: str, *, month, period, summary, portal_url, target_env, failed_tenant_count: int) -> str` — テンプレートのプレースホルダ `{month}` `{period}` `{tenants}` `{devices}` `{billable}` `{total}` `{url}` を差し込む。`target_env == "verification"` なら先頭に `【検証環境】`。取得失敗があれば「取得失敗 N テナント（要再実行）」を追記。**URL は空行で区切る**（設計 8.5 の自動リンク対策）。**メンションは本文に含めない**（`notify.Slack(mention=...)` にだけ渡す。前提と判断 17）。
- `def failure_message(month: str, error: str, portal_url: str) -> tuple[str, str]` — （件名, 本文）。設計 6.8 の「【SaaS_A 月額課金レポート】YYYY年MM月分の集計に失敗しました。原因: <エラー要約>。手動実行: <ポータル URL>」。
- `def retry_exhausted_message(month: str, attempts: int, portal_url: str) -> tuple[str, str]` — 「【要手動】YYYY年MM月分の自動再実行が N 回失敗しました。自動再実行を停止しました。手動実行: <ポータル URL>」。
- `def attachment_filename(month: str, target_env: str) -> str` — `saas_a_billing_2026-10.xlsx` / `saas_a_billing_2026-10_verification.xlsx`。**ASCII 名**（設計 8.2）。
- `def portal_url(env: str, target_env: str, month: str) -> str` — `https://backoffice{-dev}.example-cloud.com/saas-a-billing/runs/{target_env}/{month}`。
- 定数 `DEFAULT_SLACK_TEMPLATE` — 設計 3.6 の既定文。
- `def validate_slack_template(template: str) -> None` — 許可プレースホルダ以外があれば `ValueError`（`PUT /settings` の検証で使う）。

**テストケース:**
- `test_mail_subject_production` — `【SaaS_A】月額ライセンス料金一覧 2026年10月分（10/20締め）`。
- `test_mail_subject_verification_has_suffix` — 末尾 ` [検証環境]`。
- `test_mail_subject_retry_has_resend_note` — `attempt=2` で「再送（第 2 回）」。
- `test_mail_subject_uses_cutoff_day` — `cutoff_day=25` で `（10/25締め）`。
- `test_mail_body_contains_all_summary_fields` — 9 項目すべてが本文に現れる。
- `test_mail_body_shows_display_period` — `2026/09/21 00:00:00 〜 2026/10/20 23:59:59`（`end` ではなく `display_end`）。
- `test_mail_body_lists_failed_tenants`
- `test_mail_body_contains_portal_url`
- `test_slack_text_fills_all_placeholders` — 7 個すべて。
- `test_slack_text_verification_prefix` — 先頭 `【検証環境】`。
- `test_slack_text_production_has_no_prefix`
- `test_slack_text_does_not_include_mention` — `mention` 引数を取らず、出力に `<!here>` / `<@...>` が現れない（メンションは `notify.Slack(mention=...)` の役割）。
- `test_slack_text_appends_failed_tenants`
- `test_slack_text_separates_url_with_blank_line`
- `test_failure_message_contains_error_and_url`
- `test_retry_exhausted_message_contains_attempts`
- `test_attachment_filename_production` / `test_attachment_filename_verification`
- `test_attachment_filename_is_ascii` — `filename.isascii()` が True。
- `test_portal_url_dev` / `test_portal_url_prod`
- `test_validate_slack_template_accepts_known_placeholders`
- `test_validate_slack_template_rejects_unknown_placeholder` — `{secret}` は `ValueError`。
- `test_default_slack_template_matches_design` — 設計 3.6 の既定文と一致。

**手順:** テスト作成 → 失敗確認 → 実装 → 全 passed → CRLF 変換・確認 → `ruff check apps/` → コミット（`mod refs #1001 saas-a-billing の messages（文面組立）を追加`）。

**受け入れ条件:** 上記 24 件 passed。設計 3.6 / 6.8 の文面と一致。CRLF。

---

## Phase B: 外部 I/O

### Task B-1: `saasa_client.py`（SaaS_A API クライアント）

**目的:** 設計 4 章 / 6.4 の API 呼び出しを実装する。ページング、7 日窓、LimitOver の再帰分割、地点単位フォールバック。

**Files:** Create `functions/saas_a_billing/saasa_client.py`, `tests/unit/test_saasa_client.py`

**Spec:** 設計 4 章（エンドポイント表）、6.4（`export_received` / `fetch_window` / `fetch_per_device`）、12.3（秘密の扱い）、13 章。応答の形は Global Constraints の「SaaS_A API」表。

**公開インターフェース:**
- `@dataclass class SaasACredentials: base_url: str; user_id: str; password: str` — `from_ssm(ssm_prefix: str, target_env: str) -> SaasACredentials`（Powertools `get_parameters(..., decrypt=True, max_age=300)`。3 件が揃わなければ `TargetEnvNotConfiguredError`）
- `class TargetEnvNotConfiguredError(Exception)` / `class SaasAApiError(Exception)`（`status_code` / `error_status` / `message` を持つ）/ `class LimitOverError(SaasAApiError)`（`result_count` / `limit_count`）/ `class TimeBudgetExceeded(Exception)`
- `class SaasAClient:`
  - `__init__(self, base_url: str, token: str | None = None, session: requests.Session | None = None)` — `Retry(total=3, backoff_factor=1, status_forcelist=[429,500,502,503,504], respect_retry_after_header=True, allowed_methods=frozenset(["GET","POST"]))` を `HTTPAdapter` で両スキームにマウント。timeout は `(5, 120)`。**401 は再試行しない**（`status_forcelist` に入れない）。
  - `def get_token(self, user_id: str, password: str) -> tuple[str, str]` — `POST /api/v2/getToken`。戻り値は `(access_token, expires_at)`。**トークンをログに出さない。**
  - `def list_tenants(self) -> list[dict]` — `GET /api/v2/tenant/list` を `data.count` に達するまでページング（`perPage=100`）。
  - `def list_devices(self, tenant_id: str) -> list[dict]` — `perPage=999` で `data.count` に達するまで。
  - `def list_groups(self, tenant_id: str) -> list[dict]` — `groups` 配列を返す。
  - `def export_window(self, tenant_id: str, start: date, end: date, device_cd: str = "all") -> requests.Response` — `stream=True` で返す。呼び出し側が `iter_lines()` する。400 + `Error.Export.LimitOver` は `LimitOverError` に変換。
- `def collect_export(client, tenant_id: str, devices: list[str], period: ClosingPeriod, deadline: Callable[[], float], window_days: int = 7) -> ExportResult` — 設計 6.4 の `export_received` / `fetch_window` / `fetch_per_device` を実装する。
  - 窓ごとに `export_window` → `LimitOverError` なら `split_window` で半分にして再帰。**1 日窓でも超える場合は `devices - received` の地点単位で取得**する。地点単位で `LimitOverError` が出たら「受信あり確定」として `received` に足す。
  - 200 + 空ボディ / `NoRecords` は「受信なし」で次の窓へ。
  - 各窓の開始前に `deadline()` が負なら `TimeBudgetExceeded`。
  - 環境変数 `EXPORT_WINDOW_DAYS`（既定 7）で窓幅を上書きできる。
- ヘッダは `Authorization: Bearer <token>`。**`requests` のログレベルを上げない。**

**テストケース（`responses` ライブラリで HTTP をモックする。設計 13 章の観点）:**
- `test_get_token_returns_access_token_and_expires_at`
- `test_get_token_401_raises_without_retry` — `responses` の呼び出し回数が 1 回であることを assert。
- `test_get_token_does_not_log_password` — `caplog` にパスワードが現れない。
- `test_list_tenants_single_page`
- `test_list_tenants_pages_until_count_reached` — `count=250`, `perPage=100` → 3 回呼ぶ。
- `test_list_devices_uses_per_page_999`
- `test_list_devices_pages_until_count_reached`
- `test_list_groups_returns_groups_array`
- `test_export_window_builds_expected_query` — `tenant_id` / `start_date` / `end_date` / `group_cd=all` / `device_cd=all` / `push_type=csv` / `output_type=raw_data`。
- `test_export_window_limit_over_raises_with_counts` — `LimitOverError(result_count=123456, limit_count=100000)`。
- `test_collect_export_splits_window_on_limit_over` — 7 日窓で LimitOver → 3 日 + 4 日で再呼び出しされる（**呼び出し順序**を assert）。
- `test_collect_export_recursive_split_until_one_day`
- `test_collect_export_falls_back_to_per_device_when_single_day_over` — 1 日窓でも LimitOver → 未確認の地点だけ個別取得。
- `test_collect_export_per_device_limit_over_marks_received` — 地点単位で LimitOver → その地点を `received` に足す。
- `test_collect_export_skips_already_received_devices_in_per_device_mode`
- `test_collect_export_handles_no_records_json`
- `test_collect_export_handles_empty_body`
- `test_collect_export_merges_all_windows` — 5 窓の結果が合成される。
- `test_collect_export_raises_time_budget_exceeded` — `deadline()` が負を返すと例外。
- `test_retries_on_429_and_5xx` — 500 → 200 で成功する（試行回数を assert）。
- `test_does_not_retry_on_401`
- `test_from_ssm_reads_three_parameters`
- `test_from_ssm_raises_when_any_parameter_missing` — `TargetEnvNotConfiguredError`。
- `test_from_ssm_uses_target_env_in_key` — `.../targets/verification/base-url` と `.../targets/production/base-url` の違い。

**手順:** OpenAPI（Global Constraints の表）を確認 → テスト作成 → 失敗確認 → 実装 → 全 passed → CRLF 変換・確認 → `ruff check apps/` → コミット（`mod refs #1001 saas-a-billing の saasa_client（API クライアント）を追加`）。

**受け入れ条件:** 上記 24 件 passed。**上限行数をハードコードせず 400 のメッセージから読む**（要確認 #3）。秘密がログに出ない。CRLF。

---

### Task B-2: `repo.py`（DynamoDB アクセス）

**目的:** 設計 7.1 のアイテム種別とアクセスパターン、6.9 の条件付き書き込みを実装する。

**Files:** Create `functions/saas_a_billing/repo.py`, `tests/integration/test_repo.py`

**Spec:** 設計 7.1（テーブル・アイテム種別・アクセスパターン）、6.9（RUN の状態遷移と条件式の表）、3.3（設定の既定値）、2.3（単価表の既定値）。

**公開インターフェース（`client("dynamodb")` を使う。前提と判断 5）:**
- `class VersionConflictError(Exception)` / `class RunAcquisitionError(Exception)`（`reason` に `ALREADY_RUNNING` / `ALREADY_SUCCEEDED`）
- `def get_settings() -> dict` — `GetItem PK=SETTINGS SK=current`。**未作成なら設計 3.3 の既定値を `version=0` で seed して返す**（`slack_channel_id` は空、`auto_run_enabled` は False）。
- `def put_settings(new: dict, expected_version: int, actor: str, actor_email: str) -> dict` — `TransactWriteItems`（Update SETTINGS with `version = :expected` + Put `AUDIT#{ISO時刻}#{uuid}`）。不一致は `VersionConflictError`。
- `def list_prices() -> list[dict]` — `Query PK=PRICE, ScanIndexForward=False`。
- `def get_price_table_at(as_of: date) -> dict` — `Query PK=PRICE, SK <= EFFECTIVE#{as_of}, ScanIndexForward=False, Limit=1`。
- `def put_price(item: dict, actor: str, actor_email: str) -> dict` — `TransactWriteItems`（Put `PRICE/EFFECTIVE#...` with `attribute_not_exists(SK)` + Put AUDIT）。既存は `AlreadyExistsError`。
- `def seed_defaults(actor: str = "system") -> None` — SETTINGS と `PRICE/EFFECTIVE#2025-09-01`（設計 2.3 の #2001）を無ければ作る。
- `def get_run(target_env: str, month: str) -> dict | None`
- `def list_runs(target_env: str | None = None, limit: int = 24) -> list[dict]` — `target_env` 省略時は両方を Query してマージし月降順。
- `def acquire_run(target_env, month, *, trigger: str, force: bool, run_token: str, requested_by=None, requested_by_email=None, distribute=True, stale_before: datetime) -> dict` — 設計 6.9 の 3 パターン（新規 PutItem `attribute_not_exists(PK)` / 再実行 UpdateItem 条件付き / `force=true` は条件式なし）。`trigger == "scheduled"` なら `auto_attempts` も +1。取得不可は `RunAcquisitionError`。
- `def start_run(target_env, month, attempt, run_token) -> None` — `queued` → `running`（条件 `#status = queued AND run_token = :token`）。
- `def set_execution_arn(target_env, month, attempt, execution_arn) -> None` — RUN に `execution_arn` を書く。**条件は `attempt = :attempt` のみ**（`Init` が `queued` → `running` の直後に書くので状態は見ない。前提と判断 14）。
- `def set_token(target_env, month, attempt, token, expires_at)` / `def clear_token(...)` / `def set_progress(..., tenants_total, tenants_done)` — いずれも条件 `#status = running AND attempt = :attempt`。
- `def finish_success(..., summary: dict, s3_xlsx_key, s3_result_key, closing_period, price_effective_from, metrics) -> None` — `succeeded`（条件 `#status = running AND attempt = :attempt`）。
- `def finish_distributed(..., notify_message_id, notify_channels, distribute_note) -> None` — `distributed`（条件 `#status = succeeded AND attempt = :attempt`）。
- `def finish_failed(..., error: str) -> None` — `failed`（条件 `attempt = :attempt`）。
- `def mark_retry_alert_sent(target_env: str, month: str) -> None` — RUN の `retry_alert_sent` を `True` にする（条件 `attribute_exists(PK)`）。上限到達アラームを 1 回だけ送るために使う（前提と判断 15）。
- `def list_audits(limit: int = 50) -> list[dict]` — 監査アイテムを新しい順（`ScanIndexForward=False`）に最大 `limit` 件。**`GET /audits`（Task E-2）が使う。**
- `def is_stale(run: dict, now: datetime) -> bool` — `status in (queued, running)` かつ `started_at` が前日以前（設計 6.2）。
- ヘルパ `def to_python(item: dict) -> dict` / `def to_dynamo(value) -> dict` — `Decimal` ↔ `int` の変換を含む。
- 環境変数 `TABLE_NAME`。

**テストケース（`moto` の DynamoDB。設計 13 章の観点）:**
- `test_get_settings_seeds_defaults_when_absent` — `version == 0`、設計 3.3 の既定値と一致、`slack_channel_id` が空、`auto_run_enabled` が False。
- `test_get_settings_returns_existing`
- `test_put_settings_increments_version_and_writes_audit`
- `test_put_settings_version_mismatch_raises_conflict`
- `test_put_settings_audit_records_actor_and_before_after`
- `test_seed_defaults_creates_price_table_2001` — 4 帯 + カメラ、`effective_from == "2025-09-01"`。
- `test_seed_defaults_is_idempotent`
- `test_get_price_table_at_picks_latest_on_or_before`
- `test_get_price_table_at_ignores_future`
- `test_put_price_rejects_duplicate_effective_from`
- `test_acquire_run_creates_new_queued_run` — `attempt == 1`、`status == "queued"`。
- `test_acquire_run_scheduled_sets_auto_attempts_1`
- `test_acquire_run_manual_does_not_increment_auto_attempts` — 設計 6.2。
- `test_acquire_run_on_failed_increments_attempt`
- `test_acquire_run_on_succeeded_without_force_raises_already_succeeded`
- `test_acquire_run_on_running_without_force_raises_already_running`
- `test_acquire_run_on_stale_running_succeeds` — `started_at` が前日以前なら奪取できる。
- `test_acquire_run_with_force_bypasses_condition` — `succeeded` でも `attempt+1` で取得。
- `test_run_keys_are_separated_by_target_env` — `RUN#verification` と `RUN#production` が独立（設計 11.2）。
- `test_start_run_requires_matching_run_token`
- `test_set_token_and_clear_token`
- `test_token_is_removed_by_clear_token` — 属性が消える（`REMOVE`）。
- `test_finish_success_requires_running_and_attempt`
- `test_finish_distributed_requires_succeeded`
- `test_finish_failed_only_requires_attempt`
- `test_set_execution_arn_records_arn_with_attempt_condition`
- `test_set_execution_arn_rejects_stale_attempt` — `attempt` 不一致では書けない。
- `test_mark_retry_alert_sent_sets_flag` — `retry_alert_sent` が `True` になる（既定は `False`）。
- `test_list_audits_returns_latest_first` — 新しい順、`limit` 件で打ち切る。
- `test_list_runs_returns_month_descending`
- `test_list_runs_without_target_env_merges_both`
- `test_is_stale_true_for_yesterday_running` / `test_is_stale_false_for_today_running`
- `test_decimal_values_are_returned_as_int`

**手順:** テスト作成 → 失敗確認 → 実装 → 全 passed → CRLF 変換・確認 → `ruff check apps/` → コミット（`mod refs #1001 saas-a-billing の repo（DynamoDB アクセス）を追加`）。

**受け入れ条件:** 上記 34 件 passed。**設計 6.9 の条件式の表がすべてテストで固定されている。** CRLF。

---

### Task B-3: `storage.py`（S3 アクセス）

**目的:** 設計 6.7 / 7.2 の S3 レイアウトと署名付き URL を実装する。

**Files:** Create `functions/saas_a_billing/storage.py`, `tests/integration/test_storage.py`

**Spec:** 設計 6.7（S3 保存の表）、7.2（レイアウト）、8 章（`GET /runs/{month}/download` の署名付き URL 300 秒）。

**公開インターフェース:**
- `def prefix(target_env: str, month: str) -> str` — `{S3_PREFIX}/{target_env}/{YYYY}/{MM}`（`S3_PREFIX` は環境変数、既定 `saas-a-billing`）。
- `def part_key(target_env, month, tenant_cd) -> str` → `.../parts/{tenant_cd}.json`
- `def xlsx_key(target_env, month) -> str` → `.../saas_a_billing_{YYYY-MM}[_verification].xlsx`（`messages.attachment_filename` を使う）
- `def result_key(target_env, month) -> str` → `.../result.json`
- `def put_part(target_env, month, tenant_cd, data: dict) -> str` — `application/json`、UTF-8、`ensure_ascii=False`。
- `def get_part(target_env, month, tenant_cd) -> dict` — `NoSuchKey` は `PartNotFoundError`。
- `def put_xlsx(target_env, month, data: bytes) -> str` — `Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`。
- `def put_result(target_env, month, data: dict) -> str` / `def get_result(target_env, month) -> dict | None`（無ければ `None`）
- `def presign_xlsx(key: str, filename: str, expires: int = 300) -> tuple[str, datetime]` — `get_object` の署名付き URL。`ResponseContentDisposition: attachment; filename="{filename}"`。戻り値は `(url, expires_at)`。
- `class PartNotFoundError(Exception)`
- 環境変数 `ATTACHMENTS_BUCKET`、`S3_PREFIX`。

**テストケース（`moto` の S3）:**
- `test_prefix_uses_target_env_and_month` — `saas-a-billing/production/2026/10`。
- `test_verification_and_production_prefixes_are_separate` — 設計 6.7。
- `test_part_key_format` / `test_result_key_format`
- `test_xlsx_key_production` — `saas_a_billing_2026-10.xlsx`。
- `test_xlsx_key_verification` — `saas_a_billing_2026-10_verification.xlsx`。
- `test_put_and_get_part_roundtrip` — 日本語が化けない。
- `test_get_part_missing_raises`
- `test_put_xlsx_sets_content_type` — `head_object` で確認。
- `test_put_result_and_get_result_roundtrip`
- `test_get_result_returns_none_when_absent`
- `test_presign_url_has_300_second_expiry` — URL に `X-Amz-Expires=300`。
- `test_presign_sets_content_disposition` — URL に `response-content-disposition` が含まれ、ファイル名が入る。
- `test_presign_returns_expires_at_in_future`
- `test_re_put_overwrites_same_key` — 再実行時は上書き（設計 6.7）。

**手順:** テスト作成 → 失敗確認 → 実装 → 全 passed → CRLF 変換・確認 → `ruff check apps/` → コミット（`mod refs #1001 saas-a-billing の storage（S3 アクセス）を追加`）。

**受け入れ条件:** 上記 15 件 passed。**検証環境と本番の成果物が同じプレフィックスに混ざらない。** CRLF。

---

## Phase C: Excel 生成

### Task C-1: `excel.py`（1/2）— 書式基盤とシート1「請求照合」

**目的:** 設計 6.6 のシート1（ベンダーA請求書との突合用）を生成する。

**Files:** Create `functions/saas_a_billing/excel.py`, `tests/unit/test_excel_sheet1.py`

**Spec:** 設計 3.5（Excel レイアウト）、6.6（処理仕様ブロック・シート1・保存期間の色）。

**公開インターフェース:**
- 定数（設計 6.6 の色）: `COLOR_NO_DATA = "#FDF3DC"`、`COLOR_EXCLUDED = "#DFE9F8"`、`COLOR_FAILED = "#E0E0E0"`、`COLOR_MISSING_TEXT = "#808080"`、`COLOR_REVIEW_TEXT = "#C00000"`、`RETENTION_3Y = ("#C6EFCE", "#006100")`、`RETENTION_1Y = ("#E4DFEC", "#3F3151")`。
- 定数 `FONT_NAME = "Meiryo"`、`FONT_SIZE = 10`、`CURRENCY_FORMAT = '"¥"#,##0'`、`DATETIME_FORMAT = "yyyy/mm/dd hh:mm"`。**中華フォント禁止。**
- `def build_workbook(result: dict) -> bytes` — `result.json` 相当の dict から `io.BytesIO` に書いて bytes を返す。**`/tmp` を使わない。**
- `def spec_block_lines(result: dict) -> list[str]` — 設計 6.6 の処理仕様ブロック 7 行（対象月・集計処理日時と締め日時・集計対象と host・課金対象期間・除外ルール・使用単価表と環境と試行・取得失敗テナント）。
- `def column_width(values: Iterable[str]) -> int` — `unicodedata.east_asian_width` で全角を 2 と数えた最大幅 + 2。
- 内部: `_write_sheet1(workbook, result)` — 処理仕様 → 合計ブロック → テナントブロック（見出し・地点行・小計）→ 課金地点なしのテナント一覧 → 使用単価表 → 脚注。
- シート1 の印刷設定: A4 横（`set_landscape()` + `set_paper(9)`）、1 ページ幅（`fit_to_pages(1, 0)`）、タイトル行の繰り返し（`repeat_rows`）、フッタにページ番号（`set_footer('&C&P / &N')`）。
- 数値は**数値型**で書き、合計は Python 側で計算した値を書く（数式を使わない）。日時は JST の naive `datetime`。

**テストケース（`openpyxl` で読み戻す。設計 13 章の観点）:**
- `test_workbook_has_two_sheets_named_correctly` — `請求照合` / `全地点（管理用）`。
- `test_sheet1_starts_with_spec_block` — 7 行が先頭にあり、対象月・締め日時・集計対象・課金対象期間・除外ルール・使用単価表・取得失敗テナントを含む。
- `test_spec_block_shows_verification_host_for_verification` — 「集計対象: 検証環境（...）」。
- `test_spec_block_shows_production_host` — 「集計対象: 本番環境（saas-a.example.jp）」。
- `test_spec_block_display_period_ends_at_23_59_59`
- `test_spec_block_lists_failed_tenants_in_red_when_present`
- `test_total_block_has_count_and_amount`
- `test_total_block_has_retention_breakdown` — 3年 / 1年 の地点数と金額。
- `test_total_block_has_camera_breakdown`
- `test_tenant_header_shows_cd_name_retention_count_subtotal`
- `test_only_billable_devices_are_listed` — 通信なし・除外・取得失敗・消えた地点が**載らない**（設計 6.6）。
- `test_review_devices_are_listed_in_red` — 仮単価の地点は載り、文字色が `#C00000`。
- `test_device_row_columns` — 現場 / 地点CD / 地点名 / フォーマット / 項目数 / 保存期間 / カメラ / 単価 / 金額 / 要確認 の 10 列。
- `test_device_rows_sorted_by_group_then_device_cd`
- `test_tenants_sorted_by_tenant_cd`
- `test_amount_is_numeric_with_currency_format` — セルの型が数値、`number_format` が `"¥"#,##0`。
- `test_subtotal_equals_sum_of_device_amounts` — 値で書かれている（数式ではない）。
- `test_retention_cell_color_3y` / `test_retention_cell_color_1y` / `test_retention_unknown_is_red_text_with_label` — `不明(3年扱い)`。
- `test_font_is_meiryo_everywhere` — 全セルの `font.name == "Meiryo"`。
- `test_no_chinese_font_used` — SimSun / SimHei / Microsoft YaHei / PingFang SC / Noto Sans SC が現れない。
- `test_tenants_without_billable_devices_are_listed_at_the_end` — 「課金地点なしのテナント」に理由付きで出る。
- `test_price_table_used_is_printed`
- `test_footnotes_present` — 通信なし 0 円・除外ルール・差異要因・境界日のみ受信の意味。
- `test_column_width_counts_fullwidth_as_two`
- `test_print_settings_are_landscape_a4_fit_to_one_page_wide`

**手順:** テスト作成 → 失敗確認 → 実装 → 全 passed → CRLF 変換・確認 → `ruff check apps/` → コミット（`mod refs #1001 saas-a-billing の excel シート1（請求照合）を追加`）。

**受け入れ条件:** 上記 27 件 passed。**Meiryo が明示され中華フォントが無い。** 合計が数式でなく値。CRLF。

---

### Task C-2: `excel.py`（2/2）— シート2「全地点（管理用）」

**目的:** 設計 6.6 のシート2（判定別件数・テナント別サマリ・全地点明細のテーブル）を生成する。

**Files:** Modify `functions/saas_a_billing/excel.py`、Create `tests/unit/test_excel_sheet2.py`

**Spec:** 設計 6.6（シート2）、6.5（判定と色の表）。

**公開インターフェース:** 内部 `_write_sheet2(workbook, result)`。
- 処理仕様ブロック（シート1 と同じ `spec_block_lines`）→ 判定別件数（課金 / 通信なし / 除外 / 要確認 / 取得失敗 / 消えた地点）→ テナント別サマリ表（テナントCD / テナント名 / 保存期間（色）/ 地点数 / 課金 / 通信なし / 除外 / 要確認 / 取得失敗 / 小計）→ 全地点明細。
- 全地点明細は **XlsxWriter の `add_table`（オートフィルタ付き）**。列は 15 列（テナントCD / テナント名 / 保存期間 / 現場 / 地点CD / 地点名 / フォーマット / 項目数 / カメラ / 最終受信日時 / 判定 / 単価 / 金額 / 要確認 / 備考）。**`add_table` の `columns[]` には `header_format`（`font_name: "Meiryo"` / `font_size: 10` / `bold: True`）を必ず渡す** — 既定のテーブルヘッダ書式にはフォント指定が無く、`test_font_is_meiryo_everywhere`（Task C-1）が落ちるため。
- **ウィンドウ枠固定（`freeze_panes`）は使わない**（設計 6.6。サマリが上にあるため）。
- 行色は Python 側で判定して静的塗り（条件付き書式に頼らない）。消えた地点は灰字 `#808080` で明細の末尾に列挙する。

**テストケース（`openpyxl` で読み戻す）:**
- `test_sheet2_starts_with_spec_block`
- `test_judgement_counts_block_has_six_categories`
- `test_judgement_counts_match_summary`
- `test_tenant_summary_columns` — 10 列。
- `test_tenant_summary_subtotal_matches_devices`
- `test_detail_table_has_fifteen_columns`
- `test_detail_table_is_registered_as_excel_table_with_autofilter` — `openpyxl` の `ws.tables` に 1 件あり、`autoFilter` が設定されている。
- `test_no_freeze_panes` — `ws.freeze_panes` が `None`。
- `test_all_devices_are_listed_including_no_data_and_excluded` — シート1 と違い全部載る。
- `test_no_data_row_is_amber` — `#FDF3DC`。
- `test_excluded_row_is_blue` — `#DFE9F8`。
- `test_failed_row_is_gray` — `#E0E0E0`。
- `test_missing_devices_are_listed_in_gray_text` — `#808080`、金額列が空。
- `test_review_flags_are_red_text_with_reason` — `OVER_MAX_ITEMS` / `NO_ITEMS` / `RETENTION_UNKNOWN` が理由として書かれる。
- `test_retention_cell_color_overrides_row_color` — 設計 6.6「保存期間セルは行色より優先して塗る」。
- `test_camera_column_shows_source` — `export` / `設定` / `-`。
- `test_boundary_only_note_in_remarks` — 備考に「境界日のみ受信」。
- `test_device_description_in_remarks`
- `test_last_received_at_is_datetime_type_with_format` — 型が `datetime`、`number_format` が `yyyy/mm/dd hh:mm`、**tz-naive**。
- `test_excluded_unit_price_has_strikethrough` — 設計 6.6「除外は単価を取り消し線で参考表示」。
- `test_amount_zero_for_no_data_and_excluded`
- `test_font_is_meiryo_on_sheet2`
- `test_build_workbook_returns_bytes_and_opens_in_openpyxl`

**手順:** テスト作成 → 失敗確認 → 実装 → 全 passed → `uv run pytest apps/saas-a-billing/tests/unit --cov=saas_a_billing --cov=api --cov=batch --cov-report=term-missing` → CRLF 変換・確認 → `ruff check apps/` → コミット（`mod refs #1001 saas-a-billing の excel シート2（全地点）を追加`）。

**受け入れ条件:** 上記 23 件 passed。`add_table` によるオートフィルタがある。**要確認 #5（テーブルとサマリの共存）の結果を報告する。** CRLF。

---

## Phase D: バッチハンドラ

### Task D-1: `trigger.py`（実行判定）

**目的:** 設計 6.2 の判定ロジックを実装する。「未実行の検知」もこの判定で兼ねる。

**Files:** Create `functions/batch/handlers/trigger.py`, `tests/integration/test_trigger.py`

**Spec:** 設計 3.1（月次自動実行）、6.1（起動イベント）、6.2（実行判定の擬似コード）、11.1（自動再実行の上限到達）、11.2（冪等性）。

**公開インターフェース:**
- `@LambdaLogger.contextualize def handler(event: dict, context) -> dict` — EventBridge Scheduler からの非同期 Invoke。戻り値は `{"started": bool, "reason": str, "month": str | None, "target_env": str | None, "execution_arn": str | None}`。
- `def decide(settings: dict, now: datetime, run: dict | None) -> tuple[bool, str]` — 純粋関数。設計 6.2 の判定を実装する。戻り値は `(実行するか, 理由コード)`。理由コード: `AUTO_RUN_DISABLED` / `BEFORE_AUTO_RUN_FROM_MONTH` / `ALREADY_SUCCEEDED` / `IN_PROGRESS` / `AUTO_RETRY_EXHAUSTED` / `START`。
- 実行時: `repo.acquire_run(..., trigger="scheduled", force=False)` → `states.start_execution(stateMachineArn=STATE_MACHINE_ARN, name=f"{target_env}-{month}-{attempt}-{uuid8}", input=json.dumps({...}))`。
- **入力には必ず `target_env` を含める**（設計 6.1）。`{"trigger":"scheduled","target_env":...,"month":...,"attempt":...,"run_token":...,"distribute":true,"force":false}`。
- `AUTO_RETRY_EXHAUSTED` のときは `messages.retry_exhausted_message` で **1 回だけ**「要手動」アラームを `notify.send`（メール + Slack）する。**2 回目以降は送らない** — RUN の `retry_alert_sent` が `false`（未設定含む）のときだけ送り、送った直後に `repo.mark_retry_alert_sent()` で `true` にする（前提と判断 15）。
- 環境変数 `STATE_MACHINE_ARN`、`TABLE_NAME`、`NOTIFY_QUEUE_URL`、`ENV`、`PORTAL_URL`。

**テストケース（`moto` の DynamoDB / SQS / Step Functions。設計 13 章の観点）:**
- `test_starts_execution_when_cutoff_passed_and_no_run` — 10/21 06:00、RUN なし → `StartExecution` が 1 回。
- `test_no_start_before_cutoff` — 10/20 06:00 → 空振り（前月分は既に succeeded）。
- `test_no_start_when_auto_run_disabled` — `AUTO_RUN_DISABLED`。
- `test_no_start_before_auto_run_from_month` — `auto_run_from_month = "2026-10"` で対象月 `2026-09` → `BEFORE_AUTO_RUN_FROM_MONTH`。
- `test_no_start_when_run_succeeded` / `test_no_start_when_run_distributed`
- `test_no_start_when_run_is_running_and_not_stale` — `IN_PROGRESS`。
- `test_starts_when_run_is_stale` — `started_at` が前日以前の `running` を奪取する。
- `test_retries_failed_run_next_day` — `failed` かつ `auto_attempts < auto_retry_days` → 実行、`auto_attempts` が +1。
- `test_stops_when_auto_attempts_reaches_limit` — `auto_attempts == auto_retry_days`（既定 5）→ 空振り + 「要手動」通知が 1 通。
- `test_retry_alert_is_sent_only_once` — 2 回目の起動では通知しない。
- `test_manual_force_does_not_consume_auto_attempts` — 設計 6.2（`acquire_run` の `trigger="manual"` で `auto_attempts` が増えないことを repo 経由で確認）。
- `test_execution_input_contains_target_env` — 設計 6.1。
- `test_execution_name_format` — `{target_env}-{YYYY-MM}-{attempt}-{uuid8}`。
- `test_uses_settings_target_env` — 設定が `production` なら入力も `production`。
- `test_cutoff_change_reflected_next_day` — `cutoff_day` を 25 に変えると判定対象月が変わる。
- `test_decide_is_pure` — `decide()` が AWS を呼ばずに判定できる。

**手順:** テスト作成 → 失敗確認 → 実装 → 全 passed → CRLF 変換・確認 → `ruff check apps/` → コミット（`mod refs #1001 saas-a-billing の trigger（実行判定）を追加`）。

**受け入れ条件:** 上記 17 件 passed。設計 6.2 の擬似コードの全分岐が網羅されている。CRLF。

---

### Task D-2: `init.py`（RUN 開始・設定/単価解決・getToken・tenant/list）

**目的:** 設計 6.3 の `Init` ステートを実装する。

**Files:** Create `functions/batch/handlers/init.py`, `tests/integration/test_init.py`

**Spec:** 設計 6.3（ステートマシンの表）、6.9（RUN の状態遷移）、11.1（`Init` の失敗）、12.3（トークンの扱い）。

**公開インターフェース:**
- `@LambdaLogger.contextualize def handler(event: dict, context) -> dict`
- 入力: `{"trigger","target_env","month","attempt","run_token","distribute","force","execution_arn",...}` — `execution_arn` は ASL の `Init` の `Parameters` が `$$.Execution.Id` から注入する（Task E-1）。
- 出力: `{"run_id","target_env","month","attempt","tenants":[{"tenant_id","tenant_cd"}...],"closing_period":{...},"price_effective_from":"...","distribute":bool}` — **トークンを出力に載せない**（設計 6.3）。**`run_id` は `Init` が `f"{target_env}-{month}-{attempt}"` で組む**（RUN のキーではなく実行の識別子。ログと `notify` の `meta.run_id` に使う）。
- 処理順: (1) `repo.start_run()` で `queued` → `running`（条件付き。二重起動防止）、(1b) 入力の `execution_arn` を `repo.set_execution_arn()`（条件 `attempt = :attempt`）で RUN に書く（前提と判断 14）、(2) `repo.get_settings()`、(3) `target_env` を確定（**実行入力 > 設定値**）、(4) `SaasACredentials.from_ssm()` で 3 件の存在確認（欠ければ `TARGET_ENV_NOT_CONFIGURED` で即失敗）、(5) `period.closing_period()`、(6) `repo.get_price_table_at(締め日)`（無ければ失敗）、(7) `client.get_token()`、(8) `repo.set_token()` で RUN に保存、(9) `client.list_tenants()` 全ページ、(10) `repo.set_progress(tenants_total=len(tenants))`。
- 例外は素通しする（ステートマシンの全体 Catch が `MarkFailed` へ回す）。

**テストケース（`moto` + `responses`）:**
- `test_transitions_run_from_queued_to_running`
- `test_execution_arn_is_recorded_on_run` — 入力の `execution_arn` が RUN に保存される。
- `test_start_run_fails_when_run_token_mismatch` — 二重起動防止。
- `test_returns_tenant_id_and_cd_only` — 出力にテナントの全属性を載せない（ステート入出力サイズ対策）。
- `test_token_is_saved_to_run_item` — `api_token` / `token_expires_at` が RUN に入る。
- `test_token_is_not_in_output` — 出力 dict にトークン文字列が現れない。
- `test_token_is_not_logged` — `caplog` にトークンが現れない。
- `test_execution_input_target_env_overrides_settings` — 入力 `production` / 設定 `verification` → `production` を使う。
- `test_uses_settings_target_env_when_input_absent`
- `test_missing_ssm_raises_target_env_not_configured` — 3 件のうち 1 件でも欠けたら。
- `test_no_price_table_raises` — 締め日以前の版が無い（設計 11.1）。
- `test_price_effective_from_is_resolved_at_cutoff_date` — 締め日時点で有効な版。
- `test_closing_period_in_output_matches_settings` — `cutoff_day` / `cutoff_time` を反映。
- `test_closing_period_open_flag` — 課金対象期間が未了の月は `open: true`（設計 7.3）。
- `test_lists_all_tenant_pages` — `count=250` で 3 ページ取得。
- `test_get_token_401_propagates`
- `test_tenants_total_recorded_on_run`

**手順:** テスト作成 → 失敗確認 → 実装 → 全 passed → CRLF 変換・確認 → `ruff check apps/` → コミット（`mod refs #1001 saas-a-billing の init ハンドラを追加`）。

**受け入れ条件:** 上記 17 件 passed。**トークンが出力・ログに出ない。** CRLF。

---

### Task D-3: `collect_tenant.py`（テナント別収集）

**目的:** 設計 6.4 の `CollectTenant` を実装する。

**Files:** Create `functions/batch/handlers/collect_tenant.py`, `tests/integration/test_collect_tenant.py`

**Spec:** 設計 6.4（テナント別収集の擬似コード）、6.10（時間予算）、11.1（`CollectTenant` の失敗）、14.2（メトリクス）。

**公開インターフェース:**
- `@LambdaLogger.contextualize def handler(event: dict, context) -> dict`
- 入力（Map の `ItemSelector` が注入）: `{"run_id","target_env","month","tenant_id","tenant_cd","attempt","closing_period":{...}}`
- 出力: `{"tenant_cd": str, "devices": int, "received": int, "failed": bool}` — **件数サマリのみ**（設計 6.4）。
- 処理: (1) RUN から `api_token` を読む（**再ログインしない**）、(2) `client.list_devices(tenant_id)` 全ページ、(3) `client.list_groups(tenant_id)`、(4) `collect_export(...)` を `deadline` 付きで実行、(5) `storage.put_part()` に `TenantPart`（テナント属性 + `devices[]` の生データ + `received` / `camera` / `first_seen` / `last_seen` / `export_calls` / `export_bytes` / `status` / `error`）を書く、(6) `repo.set_progress(tenants_done + 1)`。
- `deadline = lambda: context.get_remaining_time_in_millis() / 1000 - TIME_BUDGET_MARGIN_SEC`（環境変数、既定 150）。
- 失敗時（`TimeBudgetExceeded` / API エラー / 401）は**例外を投げず** `status: "failed"` と `error` を parts に書き、戻り値 `failed: true` で返す。**ただしステートマシンの `Retry` を効かせたい一時エラー（5xx / 接続エラー）は例外を投げる**（`Retry` 2 回 → `Catch` で `failed: true`）。
- Powertools Metrics（名前空間 `example-backoffice/saas-a-billing`）: `TenantDuration` / `ExportCalls` / `ExportBytes`。
- 環境変数 `EXPORT_WINDOW_DAYS`（7）、`TIME_BUDGET_MARGIN_SEC`（150）。

**テストケース（`moto` + `responses`）:**
- `test_writes_part_to_s3_with_expected_key` — `.../parts/{tenant_cd}.json`。
- `test_return_value_contains_only_counts` — 戻り値のキーが 4 つだけ。
- `test_reads_token_from_run_item` — `get_token` を呼ばない（`responses` に登録が無くても動く）。
- `test_does_not_relogin_on_401` — 401 で当該テナントを失敗として記録し、`getToken` を呼ばない（設計 6.3）。
- `test_lists_all_device_pages`
- `test_part_contains_devices_groups_received_camera`
- `test_part_contains_first_and_last_seen`
- `test_export_uses_seven_day_windows` — 8/21〜9/20 で 5 回呼ばれる。
- `test_export_window_days_env_override` — `EXPORT_WINDOW_DAYS=3` で回数が変わる。
- `test_limit_over_splits_window`
- `test_time_budget_exceeded_marks_failed_with_reason` — `error == "TIME_BUDGET_EXCEEDED"`、`failed: true`、**例外を投げない**。
- `test_transient_5xx_raises_for_state_machine_retry` — 例外が上がる。
- `test_device_list_failure_writes_empty_devices` — 設計 7.3「`device/list` 自体に失敗した場合は空配列」。
- `test_progress_is_updated_on_run`
- `test_metrics_are_emitted` — `TenantDuration` / `ExportCalls` / `ExportBytes`。
- `test_token_is_not_logged`

**手順:** テスト作成 → 失敗確認 → 実装 → 全 passed → CRLF 変換・確認 → `ruff check apps/` → コミット（`mod refs #1001 saas-a-billing の collect_tenant ハンドラを追加`）。

**受け入れ条件:** 上記 16 件 passed。**時間予算超過でハードタイムアウトしない**（設計 6.10）。CRLF。

---

### Task D-4: `aggregate.py`（集約・判定・Excel・配布）

**目的:** 設計 6.3 の `Aggregate` を実装する。本アプリの中心。

**Files:** Create `functions/batch/handlers/aggregate.py`, `tests/integration/test_aggregate.py`

**Spec:** 設計 6.3、6.5（判定）、6.6（Excel）、6.7（S3）、6.8（通知）、6.9（RUN 更新）、7.3（`result.json` スキーマ）、11.1。

**公開インターフェース:**
- `@LambdaLogger.contextualize def handler(event: dict, context) -> dict`
- 入力: `Init` の出力 + `CollectTenants` の Map 出力（`[{"tenant_cd","devices","received","failed"}...]`）。
- 出力: `{"run_id","month","target_env","summary":{...},"s3_xlsx_key","s3_result_key","distributed":bool}`。
- 処理: (1) **Map 出力の `tenant_cd` からキーを組み立てて** parts を読む（`ListObjects` に依存しない。設計 6.3）、(2) 前回 RUN（**同一 `target_env` の直前の対象月で `succeeded`/`distributed`**）の `result.json` を読み `billing.missing_devices()`、(3) 全地点を `billing.classify()`、(4) `billing.summarize()`、(5) `result.json` を組んで S3 へ、(6) `excel.build_workbook()` → S3 へ、(7) `repo.finish_success()`、(8) `distribute` なら `notify.send()` で配布する（添付は **`notify.Attachment(key=storage.xlsx_key(target_env, month), filename=messages.attachment_filename(month, target_env), content_type=...)`** で (6) が置いた既存キーを参照する。**`notify.put_attachment()` は呼ばない**＝同じ Excel を二重保存しない。前提と判断 18）→ `repo.finish_distributed()`、(9) RUN の `api_token` / `token_expires_at` を削除。
- 通知: Slack は `notify.Slack(text=messages.slack_text(...), channel=settings["slack_channel_id"], mention=settings.get("slack_mention"))` で送る（**メンションは `slack_text` に含めず `mention` 引数で渡す**。前提と判断 17）。`slack_channel_id` が空なら `channels` から `slack` を外し、`distribute_note: "Slack 未設定"` を RUN に残す（設計 6.8）。`notify` の SQS 投函に失敗しても **`succeeded` のまま**にして `error` に記録する（設計 11.1）。
- Powertools Metrics: `RunDuration` / `TenantCount` / `TenantFailed` / `MissingDevices` / `TotalAmount`。

**テストケース（`moto` + parts フィクスチャ）:**
- `test_reads_parts_by_key_not_by_listing` — S3 の `list_objects_v2` を呼ばない。
- `test_result_json_matches_schema` — 設計 7.3 のトップレベルキーがすべてある（`schema` は `saas-a-billing.result.v1`）。
- `test_result_json_devices_have_all_fields` — 設計 7.3 の `devices[]` の 19 フィールド（`DeviceJudgement`（A-4）と 1:1）。
- `test_summary_counts_match_devices`
- `test_missing_devices_from_previous_month_same_target_env` — 前月の `result.json` を読む。
- `test_missing_devices_does_not_cross_target_env` — `verification` の前回は `production` の差分に使われない（設計 7.1 末尾）。
- `test_no_previous_run_yields_empty_missing_devices`
- `test_failed_tenant_is_counted_and_devices_marked_failed`
- `test_xlsx_is_written_to_s3_with_correct_key`
- `test_result_json_is_written_to_s3`
- `test_run_becomes_succeeded_with_summary_and_keys`
- `test_token_attributes_are_removed_from_run`
- `test_distribute_true_sends_notify_with_attachment` — SQS に 1 件、`attachments[0].key` が `storage.xlsx_key()` と一致し、`attachments[0].filename` が `messages.attachment_filename()` と一致。
- `test_attachment_reuses_existing_key_without_reupload` — `notify.put_attachment()` を呼ばず、添付バケットの xlsx オブジェクトが 1 つだけ（二重保存しない）。
- `test_slack_mention_is_sent_as_field_not_in_text` — `slack.mention` に入り、`slack.text` にはメンション文字列が無い。
- `test_distribute_false_does_not_notify` — SQS が空、RUN は `succeeded` 止まり。
- `test_run_becomes_distributed_after_notify`
- `test_slack_channel_empty_drops_slack_channel` — `channels == ["email"]`、`distribute_note == "Slack 未設定"`。
- `test_verification_subject_has_suffix_and_filename_has_verification` — 設計 6.8。
- `test_production_has_no_verification_markers`
- `test_notify_failure_keeps_succeeded_and_records_error`
- `test_settings_snapshot_recorded_in_result` — `exclude_keywords` / `camera_format_names`。
- `test_price_table_recorded_in_result`
- `test_metrics_are_emitted`
- `test_closing_period_open_true_for_unfinished_month`
- `test_attempt_greater_than_one_marks_resend_in_subject`

**手順:** テスト作成 → 失敗確認 → 実装 → 全 passed → CRLF 変換・確認 → `ruff check apps/` → コミット（`mod refs #1001 saas-a-billing の aggregate ハンドラを追加`）。

**受け入れ条件:** 上記 26 件 passed。`result.json` が設計 7.3 のスキーマと一致。CRLF。

---

### Task D-5: `mark_failed.py`（失敗記録と通知）

**目的:** 設計 6.3 の `MarkFailed` を実装する。

**Files:** Create `functions/batch/handlers/mark_failed.py`, `tests/integration/test_mark_failed.py`

**Spec:** 設計 6.3、6.8（失敗通知の文面）、11.1。

**公開インターフェース:**
- `@LambdaLogger.contextualize def handler(event: dict, context) -> dict` — 入力にステートマシンの `$.error`（`{"Error": "...", "Cause": "..."}`）を含む。
- 処理: (1) エラー要約を作る（`Cause` の JSON から `errorMessage` を取り出す。**取り出せなければ `Error` をそのまま使う。長すぎる場合は 1,000 文字で切る**）、(2) `repo.finish_failed()`、(3) RUN の `api_token` / `token_expires_at` を削除、(4) `messages.failure_message()` で Slack + `mail_to` に通知。
- **通知自体の失敗はログのみ**（設計 6.3 / 11.1）。例外を投げない。
- 出力: `{"run_id","month","target_env","status":"failed","error": str}`。

**テストケース:**
- `test_run_becomes_failed_with_error_summary`
- `test_error_summary_extracted_from_cause_json`
- `test_error_summary_falls_back_to_error_field`
- `test_error_summary_is_truncated`
- `test_token_attributes_are_removed`
- `test_sends_failure_notification_to_mail_and_slack`
- `test_failure_notification_contains_portal_url`
- `test_notify_failure_is_swallowed_and_logged` — SQS が落ちても例外にしない。
- `test_slack_channel_empty_sends_email_only`
- `test_target_env_not_configured_error_is_reported` — `TARGET_ENV_NOT_CONFIGURED` が本文に出る。
- `test_secrets_are_not_included_in_error_summary` — トークン・パスワードが本文・ログに出ない。

**手順:** テスト作成 → 失敗確認 → 実装 → 全 passed → `uv run pytest apps/saas-a-billing/tests --cov=saas_a_billing --cov=api --cov=batch --cov-report=term-missing --cov-fail-under=80` → CRLF 変換・確認 → `ruff check apps/` → コミット（`mod refs #1001 saas-a-billing の mark_failed ハンドラを追加`）。

**受け入れ条件:** 上記 11 件 passed。`saas_a_billing` / `api` / `batch` の 3 パッケージ合計でカバレッジ 80% 以上（設計 13 章）。CRLF。

---

## Phase E: API とインフラ

### Task E-1: ステートマシン定義 `run.asl.json`

**目的:** 設計 6.3 のステートマシンを ASL で定義する。

**Files:** Create `apps/saas-a-billing/statemachine/run.asl.json`, `tests/infra/test_saas_a_billing_statemachine.py`

**Spec:** 設計 6.3（ステートの表）、6.4（Map の並列度）、11.1（`CollectTenant` の Retry / Catch）。

**構造:**
- `StartAt: "Init"`、`Comment` にアプリ名。
- `Init` — `Type: Task`、`Resource: ${InitFunctionArn}`、`Next: CollectTenants`。`Parameters` で実行入力をそのまま渡しつつ `execution_arn`（`$$.Execution.Id`）を足す（下の JSON 例。前提と判断 14）。
- `CollectTenants` — `Type: Map`、`ItemsPath: "$.tenants"`、`MaxConcurrency: 2`、`ItemSelector` で `run_id` / `target_env` / `month` / `attempt` / `closing_period` / `tenant_id` / `tenant_cd` を各要素に注入、`ResultPath: "$.collected"`、`Next: Aggregate`。`ItemProcessor` の中は単一ステート `CollectTenant`（`Type: Task`、`Resource: ${CollectTenantFunctionArn}`、`Retry: [{"ErrorEquals":["States.TaskFailed"],"MaxAttempts":2,"IntervalSeconds":5,"BackoffRate":2.0}]`、`Catch: [{"ErrorEquals":["States.ALL"],"ResultPath":"$.error","Next":"MarkTenantFailed"}]`）と `MarkTenantFailed`（`Type: Pass`、`Parameters` で `tenant_cd` / `failed: true` を返す。**`ResultPath: "$.error"` により元入力の `tenant_cd` が保持される**。設計 6.3）。
- `Aggregate` — `Type: Task`、`Resource: ${AggregateFunctionArn}`、`End: true`。
- **全体 Catch:** `Init` / `CollectTenants` / `Aggregate` それぞれに `Catch: [{"ErrorEquals":["States.ALL"],"ResultPath":"$.error","Next":"MarkFailed"}]`。
- `MarkFailed` — `Type: Task`、`Resource: ${MarkFailedFunctionArn}`、`End: true`。
- `TimeoutSeconds` をステートマシン全体に設定（例 `7200`）。

**`Init` の `Parameters`（JSON 例）:**

```json
{
  "trigger.$": "$.trigger",
  "target_env.$": "$.target_env",
  "month.$": "$.month",
  "attempt.$": "$.attempt",
  "run_token.$": "$.run_token",
  "distribute.$": "$.distribute",
  "force.$": "$.force",
  "execution_arn.$": "$$.Execution.Id"
}
```

**`CollectTenants` の `ItemSelector`（JSON 例）:**

```json
{
  "run_id.$": "$.run_id",
  "target_env.$": "$.target_env",
  "month.$": "$.month",
  "attempt.$": "$.attempt",
  "closing_period.$": "$.closing_period",
  "tenant_id.$": "$$.Map.Item.Value.tenant_id",
  "tenant_cd.$": "$$.Map.Item.Value.tenant_cd"
}
```

**テストケース（`tests/infra/test_saas_a_billing_statemachine.py`。`json.load` で読む。前提と判断 7）:**
- `test_is_valid_json`
- `test_start_at_is_init`
- `test_states_are_init_collect_tenants_aggregate_mark_failed`
- `test_map_items_path_is_tenants`
- `test_map_max_concurrency_is_2` — 設計 6.4。
- `test_map_item_selector_injects_run_context` — `ItemSelector` のキーが `run_id.$` / `target_env.$` / `month.$` / `attempt.$` / `closing_period.$` / `tenant_id.$` / `tenant_cd.$` の 7 つで、テナント側の 2 つが `$$.Map.Item.Value.*` を参照している。
- `test_init_parameters_inject_execution_arn` — `Init.Parameters` に `"execution_arn.$": "$$.Execution.Id"` がある（前提と判断 14）。
- `test_collect_tenant_has_retry_two_attempts_backoff_2`
- `test_collect_tenant_catch_uses_result_path_error` — 元入力が保持される（設計 6.3）。
- `test_map_continues_after_tenant_failure` — Catch の遷移先が Map の内側で終端し、Map 自体は失敗しない。
- `test_init_catches_all_to_mark_failed`
- `test_aggregate_catches_all_to_mark_failed`
- `test_mark_failed_is_terminal`
- `test_all_resources_use_substitutions` — `${InitFunctionArn}` 等 4 つの置換変数のみを使い、ARN をハードコードしていない。
- `test_state_machine_has_timeout`

**手順:** テスト作成 → 失敗確認 → 実装 → 全 passed → CRLF 変換・確認 → コミット（`mod refs #1001 saas-a-billing のステートマシン定義を追加`）。

**受け入れ条件:** 上記 15 件 passed。ARN のハードコードが無い。CRLF。

---

### Task E-2: `api/app.py`（HTTP API のルート）

**目的:** 設計 8 章の 8 エンドポイントに `GET /audits`（前提と判断 13）を加えた **9 エンドポイント**を実装する。

**Files:** Create `functions/api/app.py`, `tests/integration/test_api.py`

**Spec:** 設計 8 章（API 設計の表）、12.1（認証・認可）、9 章（画面が使う API）。

**公開インターフェース:**
- `app = APIGatewayHttpResolver(strip_prefixes=["/api/saas-a-billing"])`
- `@LambdaLogger.contextualize @origin_verify.origin_verify def handler(event, context): return app.resolve(event, context)`
- ルート（**すべて shared の `response.ok` / `response.error` / `response.json_response` でエンベロープを返す**）:

| メソッド / パス | 権限 | 挙動 |
| --- | --- | --- |
| `GET /settings` | 全ユーザー | `repo.get_settings()`（未作成なら seed）+ `saasa_host`（現在の `target_env` の base-url のホスト名のみ）+ `targets`（`{verification: bool, production: bool}`。SSM 3 件の登録状況）+ `slack_mode`（`/example-backoffice/{env}/shared/slack/mode`）。**base-url 以外の秘密は読まない** |
| `PUT /settings` | `admin` | 設計 8 章の検証をすべて実施 → `repo.put_settings()`。`version` 不一致は 409 `VERSION_CONFLICT` |
| `GET /prices` | 全ユーザー | `{versions: [...], current: {...}}` |
| `POST /prices` | `admin` | 検証（`max_items` 昇順・重複なし・単価が 0 以上の整数）→ `repo.put_price()`。同日既存は 409 `ALREADY_EXISTS` |
| `GET /runs` | 全ユーザー | `?limit=24&target_env=`。stale な `queued`/`running` は `status: "failed"`, `error: "TIMEOUT(推定)"` に読み替えて返す |
| `GET /runs/{month}` | 全ユーザー | `?target_env=`（既定は設定値）、`?detail=true` → RUN + `result.json` |
| `POST /runs` | 全ユーザー | `{month, target_env?, distribute, force}` → 検証 → `repo.acquire_run(trigger="manual")` → `states.start_execution()`（**実行入力・実行名は Task D-1 と同じ形。`trigger: "manual"`**）→ 202 `{month, target_env, status:"queued", attempt, execution_arn}` |
| `GET /runs/{month}/download` | 全ユーザー | `storage.presign_xlsx()` → `{url, expires_at}`。`s3_xlsx_key` が無ければ 404 |
| `GET /audits` | `admin` | `repo.list_audits(limit=50)` の直近 50 件を新しい順に返す。設定画面の変更履歴に使う。**設計 8 章に無い追加**（前提と判断 13） |

- `PUT /settings` の検証（設計 8 章）: `target_env` は 2 値のいずれかで**当該環境の SSM 3 件が登録済み**（未登録は 400 `TARGET_ENV_NOT_CONFIGURED`）、`cutoff_day` 1〜28、`cutoff_time` は `HH:MM`（`24:00` を許容）、`auto_retry_days` 1〜31、`auto_run_from_month` は `YYYY-MM`、`slack_channel_id` は必須で `C` / `G` で始まる英数字、メール宛先はメール形式、`slack_template` は `messages.validate_slack_template()`。
- `POST /runs` のエラー: 409 `ALREADY_RUNNING` / `ALREADY_SUCCEEDED`（`force=false`）、400 `TARGET_ENV_NOT_CONFIGURED`、400 `MONTH_NOT_STARTED`（課金対象期間の開始が未来）、502（`StartExecution` 失敗時は RUN を `failed` に戻す）。
- 例外ハンドラ: `auth.UnauthorizedError` → 401、`auth.ForbiddenError` → 403、`repo.VersionConflictError` → 409、その他 → 500（**スタックトレースをレスポンスに出さない**）。
- 環境変数: `TABLE_NAME`, `ATTACHMENTS_BUCKET`, `S3_PREFIX`, `STATE_MACHINE_ARN`, `SSM_PREFIX`, `ENV`, `PORTAL_URL`, `API_PREFIX`。

**テストケース（`make_api_event()` + `moto`。設計 13 章の観点）:**
- `test_get_settings_seeds_and_returns_defaults`
- `test_get_settings_includes_saasa_host_targets_and_slack_mode`
- `test_get_settings_does_not_return_password` — 応答の JSON にパスワードが現れない。
- `test_get_settings_allowed_for_non_admin`
- `test_put_settings_requires_admin` — 一般ユーザーは 403。
- `test_put_settings_updates_and_increments_version`
- `test_put_settings_version_conflict_returns_409`
- `test_put_settings_rejects_unregistered_target_env` — 400 `TARGET_ENV_NOT_CONFIGURED`。
- `test_put_settings_validates_cutoff_day_range`（0 / 29 は 400）
- `test_put_settings_accepts_cutoff_time_24_00`
- `test_put_settings_rejects_invalid_cutoff_time`
- `test_put_settings_requires_slack_channel_id_format`（`#name` は 400、`C0123ABCD` は OK）
- `test_put_settings_rejects_unknown_slack_placeholder`
- `test_put_settings_writes_audit_with_actor_email`
- `test_get_prices_returns_versions_and_current`
- `test_post_prices_requires_admin`
- `test_post_prices_rejects_unsorted_bands`
- `test_post_prices_rejects_duplicate_effective_from` — 409。
- `test_get_runs_returns_month_descending`
- `test_get_runs_filters_by_target_env`
- `test_get_runs_maps_stale_running_to_failed` — `error == "TIMEOUT(推定)"`。
- `test_get_run_detail_includes_result_json`
- `test_get_run_detail_false_returns_run_only`
- `test_get_run_not_found_returns_404`
- `test_post_runs_starts_execution_and_returns_202`
- `test_post_runs_allowed_for_non_admin`
- `test_post_runs_conflict_when_already_running` — 409。
- `test_post_runs_conflict_when_succeeded_without_force` — 409。
- `test_post_runs_force_overrides_succeeded`
- `test_post_runs_rejects_future_month` — 400 `MONTH_NOT_STARTED`。
- `test_post_runs_allows_open_period_month` — 課金対象期間が未了でも 202。
- `test_post_runs_rejects_unregistered_target_env` — 400。
- `test_post_runs_rolls_back_run_to_failed_when_start_execution_fails` — 502。
- `test_post_runs_execution_name_format`
- `test_download_returns_presigned_url_and_expiry`
- `test_download_404_when_no_xlsx_key`
- `test_get_audits_requires_admin` — 一般ユーザーは 403。
- `test_get_audits_returns_latest_fifty_descending` — 新しい順、最大 50 件。
- `test_get_audits_includes_actor_email_and_before_after`
- `test_unauthenticated_event_returns_401` — クレームなし。
- `test_origin_verify_mismatch_returns_403`

**手順:** テスト作成 → 失敗確認 → 実装 → 全 passed → CRLF 変換・確認 → `ruff check apps/` → コミット（`mod refs #1001 saas-a-billing の API ハンドラを追加`）。

**受け入れ条件:** 上記 41 件 passed。**設定変更・単価追加・監査履歴の閲覧が `admin` で保護されている**（ADD-APP-GUIDE のチェックリスト）。秘密が応答に出ない。CRLF。

---

### Task E-3: SAM テンプレートと samconfig / edge-snippet

**目的:** 設計 5.1 / 5.4 / 10 章 / 12.2 のインフラを定義する。

**Files:**
- Create: `apps/saas-a-billing/template.yaml`, `samconfig.toml`, `edge-snippet.yaml`, `README.md`, `functions/requirements.txt`
- Create: `tests/infra/test_saas_a_billing_template.py`
- Modify: `README.md`（リポジトリルート。アプリ一覧に saas-a-billing を追加する）

**Spec:** 設計 5.1（スタックと配置）、5.4（SSM 契約）、6.3（ステートマシン）、10 章（設定項目と環境変数）、12.2（IAM 最小権限）、14.2（監視）。プラットフォーム設計 4.4（app テンプレートの受け口）、13.2（samconfig）、14.1（命名）、14.2（タグ）。
**流用元:** プラットフォーム計画 Task P4-6 で作った `templates/app/` の雛形。**雛形（`templates/app`）を参考に手書きする**（cookiecutter で生成せず、雛形のパラメータ構成・タグ規約・IAM の書き方に合わせて `template.yaml` を新規に書く）。

**Parameters（設計 4.4 の 6 本 + アプリ固有）:** `ProjectName`、`Env`、`LogLevel`、`UserPoolIssuer` / `UserPoolClientId` / `NotifyQueueUrl` / `NotifyQueueArn` / `AttachmentsBucket` / `SharedLayerArn`（すべて `AWS::SSM::Parameter::Value<String>`、`Default` は `/example-backoffice/dev/...`）、`ExamplePowertoolsLayerArn`、`PowertoolsLayerArn`、`PortalUrl`。

**リソース:**
| 論理 ID | 型 | 要点 |
| --- | --- | --- |
| `HttpApi` | `AWS::Serverless::HttpApi` | `$default` ステージ、`Auth.DefaultAuthorizer: CognitoJwt`（`issuer: !Ref UserPoolIssuer`、`audience: [!Ref UserPoolClientId]`、`IdentitySource: $request.header.Authorization`）、`AccessLogSettings`（`requestId` / `path` / `status` / `sub`）、`Tags: {Project, Name: Api}` |
| `ApiAccessLogGroup` | `AWS::Logs::LogGroup` | `/aws/apigateway/example-backoffice-saas-a-billing-{env}`、365 日 |
| `ApiFunction` | `AWS::Serverless::Function` | `CodeUri: functions/`、`Handler: api/app.handler`、timeout 29、memory 512、`Events.Api`（`Path: /api/saas-a-billing/{proxy+}`、`Method: ANY`、`PayloadFormatVersion: "2.0"`） |
| `TriggerFunction` | 同上 | `Handler: batch/handlers/trigger.handler`、timeout 60、memory 256、`Events.Schedule`（`ScheduleV2`、`cron(0 6 * * ? *)`、`ScheduleExpressionTimezone: Asia/Tokyo`、`FlexibleTimeWindow: {Mode: "OFF"}`、`RetryPolicy: {MaximumRetryAttempts: 2, MaximumEventAgeInSeconds: 3600}`、`DeadLetterConfig` → `SchedulerDlq`）、`EventInvokeConfig: {MaximumRetryAttempts: 0, DestinationConfig: {OnFailure: {Destination: !GetAtt TriggerFailureQueue.Arn, Type: SQS}}}` |
| `InitFunction` / `CollectTenantFunction` / `AggregateFunction` / `MarkFailedFunction` | 同上 | timeout 900、memory 1024、Handler は `batch/handlers/{name}.handler` |
| 各 `*LogGroup` | `AWS::Logs::LogGroup` | 6 本すべて 365 日 |
| `DataTable` | `AWS::DynamoDB::Table` | `example-backoffice-saas-a-billing-data-{env}`、`PK`/`SK`（String）、`PAY_PER_REQUEST`、`SSESpecification: {SSEEnabled: true, SSEType: KMS}`、`PointInTimeRecoverySpecification`、`DeletionPolicy: Retain`、prod は `DeletionProtectionEnabled: true`（`Condition`） |
| `StateMachine` | `AWS::Serverless::StateMachine` | `DefinitionUri: statemachine/run.asl.json`、`DefinitionSubstitutions` で 4 関数の ARN、`Type: STANDARD`、`Logging`（CloudWatch Logs）、`Tags: {Project, Name: StateMachine}` |
| `StateMachineLogGroup` | `AWS::Logs::LogGroup` | 365 日 |
| `SchedulerDlq` / `TriggerFailureQueue` | `AWS::SQS::Queue` | `Tags: [Project, Name: Queue]` |
| `ApiDomainParameter` | `AWS::SSM::Parameter` | `/${ProjectName}/${Env}/apps/saas-a-billing/api-domain` = `${HttpApi}.execute-api.${AWS::Region}.amazonaws.com` |
| `StateMachineFailedAlarm` / `LambdaErrorsAlarm` / `SchedulerDlqAlarm` / `TriggerFailureAlarm` | `AWS::CloudWatch::Alarm` | 設計 14.2 のしきい値 |

**Globals:** `Function: {Runtime: python3.13, Architectures: [x86_64], Layers: [!Ref SharedLayerArn, !Ref ExamplePowertoolsLayerArn, !Ref PowertoolsLayerArn], Environment.Variables: {ENV, APP_NAME: saas-a-billing, TABLE_NAME, ATTACHMENTS_BUCKET, S3_PREFIX: saas-a-billing, NOTIFY_QUEUE_URL, PORTAL_URL, API_PREFIX: /api/saas-a-billing, SSM_PREFIX, EXPORT_WINDOW_DAYS: 7, TIME_BUDGET_MARGIN_SEC: 150（**設計 10 章の表にある `EXPORT_LIMIT_ROWS` は置かない**。上限は 400 応答の `Error.Export.LimitOver,<件数>,<上限>` から読む。要確認 #3。設計書へは後日反映）, POWERTOOLS_SERVICE_NAME: saas-a-billing, POWERTOOLS_METRICS_NAMESPACE: example-backoffice/saas-a-billing, LOG_LEVEL}, Tags: {Project: !Ref ProjectName, Name: Function}}`。`STATE_MACHINE_ARN` は `api` / `trigger` にのみ付ける。

**IAM（設計 12.2。関数ごとにロールを作る。共有ロール禁止）:** api / trigger / batch 4 関数の表のとおり。**`Resource: "*"` は `cloudwatch:PutMetricData`（`cloudwatch:namespace` 条件付き）と Logs の作成のみ。**

**`functions/requirements.txt`:** `XlsxWriter==3.2.9` と `requests>=2.32`（boto3 / Powertools はランタイム同梱・レイヤ）。

**`edge-snippet.yaml`:** ADD-APP-GUIDE 2 章ステップ 6 の 3 要素（パラメータ 1 + origin 1 + behavior 1）。**プラットフォーム計画 Task P3-3 で edge テンプレートに既に書いてあるものと一致させる。**

**samconfig:** `[default]`（dev）/ `[prod]`。`tags = "Project=example-backoffice App=saas-a-billing Env={env}"`。`use_container = true`。**prod は SSM 型パラメータの `Default` をすべて `/example-backoffice/prod/...` に上書きする**（6 本）。

**テストケース（`tests/infra/test_saas_a_billing_template.py`）:**
- `test_ssm_parameter_defaults_match_contract` — 6 本の `Default` が設計 4.2 のキー名と一致。
- `test_prod_samconfig_overrides_all_six_ssm_keys` — `samconfig.toml` を読み `[prod]` の `parameter_overrides` に 6 本すべてがある（ADD-APP-GUIDE の「よくある失敗」対策）。
- `test_http_api_has_default_jwt_authorizer`
- `test_api_route_path_matches_strip_prefix` — `/api/saas-a-billing/{proxy+}`。
- `test_api_access_log_settings_exist`
- `test_six_functions_share_one_code_uri` — `CodeUri` がすべて `functions/`。
- `test_function_timeouts_and_memory` — api 29/512、trigger 60/256、batch 4 本 900/1024（設計 5.5）。
- `test_all_functions_have_shared_layer`
- `test_each_function_has_its_own_role` — 共有ロールが無い。
- `test_no_wildcard_resource_except_logs_and_metrics`
- `test_s3_permissions_scoped_to_app_prefix` — `arn:aws:s3:::{bucket}/saas-a-billing/*`。
- `test_sqs_permission_scoped_to_notify_queue_arn`
- `test_ssm_permission_scoped_to_app_origin_verify_and_slack_mode` — `ssm:GetParameter*` の `Resource` が `apps/saas-a-billing/*` / `/edge/origin-verify-secret` / `/shared/slack/mode` の 3 つに限定される（`GET /settings` が `slack_mode` を読むため）。
- `test_dynamodb_permission_scoped_to_own_table`
- `test_start_execution_scoped_to_own_state_machine`
- `test_state_machine_role_invokes_only_four_functions`
- `test_table_has_kms_sse_and_pitr`
- `test_table_deletion_policy_is_retain`
- `test_schedule_is_cron_0600_asia_tokyo_with_flexible_off`
- `test_schedule_has_dlq`
- `test_trigger_has_zero_async_retries_and_on_failure` — 設計 11.1。
- `test_state_machine_uses_definition_uri_and_substitutions`
- `test_alarms_exist_for_state_machine_and_lambda_and_dlqs` — 4 本（設計 14.2）。
- `test_all_log_groups_retain_365_days` — 8 本すべて。
- `test_writes_api_domain_ssm_parameter`
- `test_edge_snippet_matches_edge_template` — `edge-snippet.yaml` の `PathPattern` / `CachePolicyId` / `OriginRequestPolicyId` / `AllowedMethods` が `infra/edge/template.yaml` と一致。

**手順:** 雛形（`templates/app/`）を読む → テスト作成 → skip 確認 → 実装 → `cd apps/saas-a-billing && sam validate --lint` → `uv run pytest tests/infra -v`（**タグ規約テストが `apps/*/template.yaml` を拾う**）→ `sam build --use-container`（デプロイはしない）→ CRLF 変換・確認 → コミット（`mod refs #1001 saas-a-billing の SAM テンプレートと samconfig を追加`）。

**受け入れ条件:** `sam validate --lint` が通る。上記 26 件 + タグ規約テストが passed。**タグ対応リソース全部に `Project` + `Name`、samconfig の `tags` に `Project` / `App=saas-a-billing` / `Env`。** 全 LogGroup が 365 日。CRLF。

---

## Phase F: フロントエンド

### Task F-1: マニフェスト / ルート / API クライアント / 型

**目的:** SPA にアプリを登録する（設計 9 章、ADD-APP-GUIDE ステップ 7）。

**Files:** Create `frontend/src/apps/saas-a-billing/{index.ts,routes.ts,api.ts,types.ts}`, `__tests__/{index.test.ts,api.test.ts}`

**流用元:** `frontend/src/apps/_template/`（プラットフォーム計画 Task P4-6）。`frontend/src/services/http.ts` の `createAppApi`。

**公開インターフェース:**
- `index.ts`: `defineApp({ id: 'saas-a-billing', title: 'SaaS_A 月額課金レポート', icon: 'mdi-receipt-text-outline', order: 10, requiredGroups: [], description: '...', routes, nav: [{title:'実行履歴', to:'/saas-a-billing', icon:'mdi-history'}, {title:'設定', to:'/saas-a-billing/settings', icon:'mdi-cog', requiredGroups:['admin']}] })`（設計 9 章）。
- `routes.ts`: `''` → `RunListView`（`meta.title: '実行履歴'`）、`'runs/:targetEnv/:month'` → `RunDetailView`、`'settings'` → `SettingsView`（`meta.requiredGroups: ['admin']`）。**`path` は相対、`component` はすべて `() => import()`。**
- `api.ts`: `const api = createAppApi('saas-a-billing')` と `billingApi = { getSettings, updateSettings, listPrices, createPrice, listRuns, getRun, createRun, getDownloadUrl, listAudits }`（`listAudits` は `GET /audits`。**admin のみ**。前提と判断 13）。
- `types.ts`: `Settings` / `PriceTable` / `Band` / `RunSummary` / `RunDetail` / `DeviceJudgement` / `TargetEnv = 'verification' | 'production'`。**バックエンドの `result.json`（設計 7.3）と設定（設計 7.1）に対応させる。**

**テストケース:**
- `test: マニフェストの id が saas-a-billing`
- `test: 設定の nav に requiredGroups: ['admin'] がある`（ADD-APP-GUIDE のチェックリスト）
- `test: routes の path がすべて相対（/ で始まらない）`
- `test: routes の component がすべて関数`
- `test: settings ルートの meta.requiredGroups が ['admin']`
- `test: api.ts の baseURL が /api/saas-a-billing`
- `test: listRuns が target_env と limit をクエリに載せる`
- `test: getRun が detail クエリを載せる`
- `test: createRun が month/target_env/distribute/force を body に載せる`
- `test: getDownloadUrl が target_env をクエリに載せる`
- `test: listAudits が GET /audits を呼ぶ`
- 加えて `frontend/src/apps/registry.test.ts`（既存）が引き続き通ること。

**手順:** テスト作成 → `cd frontend && npm test` で失敗確認 → 実装 → 全 passed → `npm run lint` → `npm run build` → CRLF 変換・確認 → コミット（`mod refs #1001 saas-a-billing のフロントマニフェストと API クライアントを追加`）。

**受け入れ条件:** 上記 11 件 + `registry.test.ts` が passed。`npm run build` が通る。CRLF。

---

### Task F-2: 実行履歴一覧（`RunListView`）とストア

**目的:** 設計 9 章の「実行履歴一覧」画面を作る。

**Files:** Create `frontend/src/apps/saas-a-billing/stores/runs.ts`, `views/RunListView.vue`, `components/{EnvBadge,StatusChip}.vue`, `__tests__/{runs.test.ts,RunListView.test.ts}`

**Spec:** 設計 3.4（レポート閲覧）、9 章（実行履歴一覧の項目表）、6.5（色の対応）。

**公開インターフェース:**
- `stores/runs.ts`: `defineStore('saas-a-billing/runs', ...)` — state `runs`, `loading`, `error`, `targetEnvFilter`。actions `fetchRuns(targetEnv?)`, `download(month, targetEnv)`（署名付き URL を取得して**取得直後に開く**。設計 8 章）, `createRun(payload)`, `pollRun(month, targetEnv)`（5 秒間隔、上限 10 分。前提と判断 11）。
- `EnvBadge.vue`: props `{ targetEnv: TargetEnv }` — **検証環境=オレンジ、本番=緑**（設計 3.3 / 3.4）。
- `StatusChip.vue`: props `{ status }` — `queued` / `running` / `succeeded` / `distributed` / `failed` を色付きチップで。
- `RunListView.vue`: 列は 集計対象環境バッジ / 月 / 状態 / 実行日時 / 試行回数 / テナント数 / 地点数 / 通信なし / 除外 / 要確認 / 取得失敗 / 消えた地点 / 合計金額 / 配布状況 / ダウンロード（設計 9 章）。上部に環境フィルタと「次回自動実行予定」（設定から算出）と手動実行ボタン。

**テストケース（`runs.test.ts`。`vi.mock` で `billingApi`）:**
- `test: fetchRuns が API を呼び runs に入る`
- `test: fetchRuns が targetEnv をそのまま渡す`
- `test: エラー時に error がセットされ loading が false に戻る`
- `test: download が署名付き URL を取得して window.open する`
- `test: createRun 後に pollRun が開始される`
- `test: pollRun が succeeded で停止する`
- `test: pollRun が上限時間で停止する`

**テストケース（`RunListView.test.ts`）:**
- `test: 検証環境の行にオレンジのバッジが出る`
- `test: 本番の行に緑のバッジが出る`
- `test: 環境フィルタを変えると fetchRuns が呼ばれる`
- `test: 合計金額が通貨表記で出る`
- `test: 状態に応じて色が変わる`
- `test: ダウンロードボタンが s3_xlsx_key の無い行では無効`
- `test: 次回自動実行予定が設定から算出されて表示される`

**手順:** テスト作成 → 失敗確認 → 実装 → 全 passed → `npm run lint` → `npm run build` → CRLF 変換・確認 → コミット（`mod refs #1001 saas-a-billing の実行履歴一覧画面を追加`）。

**受け入れ条件:** 上記 14 件 passed。**環境バッジ（検証=オレンジ / 本番=緑）が出る**（設計 3.4「検証環境の結果を本番と取り違えないようにする」）。CRLF。

---

### Task F-3: 月次内訳（`RunDetailView`）

**目的:** 設計 9 章の「月次内訳」画面を作る。`result.json` をそのまま描画する（設計 7.3）。

**Files:** Create `frontend/src/apps/saas-a-billing/views/RunDetailView.vue`, `components/JudgementChip.vue`, `__tests__/RunDetailView.test.ts`

**Spec:** 設計 3.4、9 章（月次内訳の項目）、6.5（判定と色）、6.6（色の値）。

**公開インターフェース:**
- `RunDetailView.vue` — ルート `/saas-a-billing/runs/:targetEnv/:month`。サマリ → テナント → 現場 → 地点のツリー表（**Excel と同じ色分け**）→ 消えた地点の一覧 → 使用単価表 → Excel ダウンロード → 再実行ボタン。フィルタ: 判定（課金 / 通信なし / 除外 / 取得失敗）・要確認のみ・境界日のみ受信。
- `JudgementChip.vue`: props `{ judgement, flags }` — 色は設計 6.6 と同じ（通信なし `#FDF3DC`、除外 `#DFE9F8`、取得失敗 `#E0E0E0`、要確認の文字 `#C00000`、消えた地点の文字 `#808080`）。

**テストケース:**
- `test: サマリの 9 項目が表示される`
- `test: テナント → 現場 → 地点の階層で表示される`
- `test: 通信なしの行がアンバー`
- `test: 除外の行が青`
- `test: 取得失敗の行が灰`
- `test: 要確認の地点が赤文字でフラグ理由付き`
- `test: 消えた地点が灰字の別ブロックに出る`
- `test: 判定フィルタで行が絞られる`
- `test: 要確認のみフィルタ`
- `test: 境界日のみ受信フィルタ`
- `test: 使用単価表が表示される`
- `test: 課金対象期間が 23:59:59 表記で出る`
- `test: 環境バッジが出る`
- `test: 再実行ボタンが確認ダイアログを出す`

**手順:** テスト作成 → 失敗確認 → 実装 → 全 passed → `npm run lint` → `npm run build` → CRLF 変換・確認 → コミット（`mod refs #1001 saas-a-billing の月次内訳画面を追加`）。

**受け入れ条件:** 上記 14 件 passed。色が設計 6.6 の値と一致。CRLF。

---

### Task F-4: 手動実行ダイアログと設定画面

**目的:** 設計 3.2 / 3.3 / 9 章の「手動実行」と「設定」を作る。

**Files:** Create `frontend/src/apps/saas-a-billing/stores/settings.ts`, `views/SettingsView.vue`, `components/{RunDialog,PriceTableEditor}.vue`, `__tests__/{settings.test.ts,RunDialog.test.ts,SettingsView.test.ts}`

**Spec:** 設計 3.2（手動実行）、3.3（設定項目の表）、8 章（`PUT /settings` の検証）、9 章（画面）、6.8（Slack 未設定の表示）。

**公開インターフェース:**
- `stores/settings.ts`: `defineStore('saas-a-billing/settings', ...)` — state `settings`, `prices`, `audits`, `slackMode`, `targets`, `saasaHost`, `loading`, `error`。actions `fetch()`, `save(patch)`（`version` を載せる。409 は「他の人が更新しました」と表示）、`addPrice(version)`、`fetchAudits()`（`GET /audits`。**admin のみ**。403 のときは `audits` を空のままにして画面にエラーを出さない）。
- `RunDialog.vue`: 集計対象環境（**SSM 未登録の環境は選択不可**。`targets` で判定）、対象月（既定は直近の締め月）、配布する/しない、強制再実行。**確認ダイアログで集計対象環境と配布時の宛先を表示する**（設計 9 章）。`force` を選んだときは**二重起動の警告**を出す（設計 6.9）。
- `SettingsView.vue`: 設計 3.3 の全項目。集計対象環境と接続先ホストを**環境バッジ付きで常時表示**。単価表の版一覧と新版追加（適用開始日）、変更履歴（監査。`GET /audits` の直近 50 件を新しい順。**`admin` のときだけブロックごと表示する**）、Slack の動作方式表示。**`admin` 以外は読み取り専用。** Webhook フォールバック中は「チャンネル ID の設定が無視される」旨を表示する（設計 3.3 / 6.8）。
- クライアント側バリデーションは UX のみ（実体はバックエンド）。`cutoff_day` 1〜28、`cutoff_time` `HH:MM`、`slack_channel_id` は `C`/`G` 始まり、メール形式、`slack_template` のプレースホルダ。

**テストケース（`settings.test.ts`）:**
- `test: fetch が settings/prices/slackMode/targets/saasaHost を取る`
- `test: save が version を載せる`
- `test: 409 でメッセージを出す`
- `test: addPrice が effective_from と bands を送る`
- `test: fetchAudits が GET /audits を呼び audits に入る`
- `test: fetchAudits が 403 でも例外にせず audits が空のまま`

**テストケース（`RunDialog.test.ts`）:**
- `test: 既定の集計対象環境が設定値`
- `test: SSM 未登録の環境が選択できない`
- `test: 既定の対象月が直近の締め月`
- `test: force を選ぶと警告が出る`
- `test: 確認ダイアログに集計対象環境と宛先が出る`
- `test: distribute=false のとき宛先を表示しない`
- `test: 実行で createRun が呼ばれる`

**テストケース（`SettingsView.test.ts`）:**
- `test: admin 以外は入力が読み取り専用`
- `test: 環境バッジと接続先ホストが常時表示される`
- `test: Webhook フォールバック中は注意書きが出る`
- `test: bot モードでは注意書きが出ない`
- `test: cutoff_day の範囲外でエラー表示`
- `test: slack_channel_id が #name 形式ならエラー表示`
- `test: 単価表の版一覧が effective_from 降順`
- `test: 新版追加で帯が昇順でないとエラー表示`
- `test: 監査履歴が表示される` — `fetchAudits()` で取得した直近 50 件が新しい順に出る。
- `test: admin 以外には変更履歴ブロックが出ない`

**手順:** テスト作成 → 失敗確認 → 実装 → 全 passed → `npm run lint` → `npm run build` → CRLF 変換・確認 → コミット（`mod refs #1001 saas-a-billing の手動実行ダイアログと設定画面を追加`）。

**受け入れ条件:** 上記 23 件 passed。**設定画面の nav / route に `requiredGroups: ['admin']` がある。** `npm run build` が通る。CRLF。

---

## Phase G: dev デプロイと検証環境での実運用確認

> **このフェーズは AWS を実際に変更する。dev のみ。** プラットフォーム計画の Phase 5 完了と、チェックポイント **CP-B1 / CP-B2**（SaaS_A 検証環境の専用 API ユーザー作成と SSM 登録）が前提。

### Task G-1: dev デプロイと直接疎通

**目的:** アプリスタックをデプロイし、`execute-api` 直叩きと CloudFront 経由の両方で API が動くことを確認する（ADD-APP-GUIDE ステップ 5・6）。

**手順:**
- [ ] **CP-B1 / CP-B2 の完了を確認する:** `aws ssm get-parameters-by-path --path /example-backoffice/dev/apps/saas-a-billing --recursive --query 'Parameters[].Name'` で `targets/verification/{base-url,user-id,password}` の 3 本があること。**値は表示しない。** 無ければユーザーに依頼して待つ。
- [ ] `cd apps/saas-a-billing && sam build --use-container && sam deploy`
- [ ] `/example-backoffice/dev/apps/saas-a-billing/api-domain` が SSM に書かれることを確認する。
- [ ] ステートマシン `saas-a-billing-run` が作られることを確認する。
- [ ] **`execute-api` 直叩き（設計 5.5 の実測。プラットフォーム計画 P5-3 から持ち越し）:**
  - [ ] `x-origin-verify` **無し**で `/api/saas-a-billing/settings` を呼ぶ → **403**。
  - [ ] `x-origin-verify` **あり**・`Authorization` 無しで呼ぶ → **401**（JWT authorizer）。
  - [ ] 両方ありで呼ぶ → **200**。
- [ ] **edge に origin と behavior を有効化する:** プラットフォーム計画 P5-2 でコメントアウトした部分を戻し、`edge-snippet.yaml` と一致していることを確認して `bash scripts/deploy-edge.sh dev`。
- [ ] `infra/edge/samconfig.toml` の `[production]` の `parameter_overrides` に `SaasABillingApiDomain=/example-backoffice/prod/apps/saas-a-billing/api-domain` があることを確認し、無ければ追記する（prod デプロイは本計画の範囲外だが、パラメータの解決先だけそろえる）。
- [ ] `bash scripts/deploy-frontend.sh dev` で SPA を再配布する。
- [ ] `https://backoffice-dev.example-cloud.com/api/saas-a-billing/settings` を ID トークン付きで呼び **200**。
- [ ] `https://backoffice-dev.example-cloud.com/saas-a-billing` を開き、サイドバーにアプリが出て実行履歴一覧が表示されることを確認する。
- [ ] コミット（`mod refs #1001 edge に saas-a-billing の origin と behavior を有効化`）。`git status --short` を確認。

**受け入れ条件:** 直叩きが 403（ヘッダ無し）/ 401（JWT 無し）/ 200（両方あり）。CloudFront 経由で 200。SPA にアプリが表示される。

---

### Task G-2: 初期設定（設定画面・単価表）

**目的:** 設計 14.1 の #6 を実施する。

**手順:**
- [ ] **チェックポイント CP-B3:** `#proj-saas-a-billing` のチャンネル ID をユーザーに確認する。**テスト段階ではテスト用チャンネルの ID を使ってよい**（本番チャンネルへの投稿は Task G-4 でユーザー確認のうえ行う）。
- [ ] `/saas-a-billing/settings` を `admin` ユーザーで開き、以下を登録する。
  - 集計対象環境: `verification`
  - 締め日 20 / 締め時刻 `24:00`
  - メール宛先 To: `devA@example.co.jp`、Cc: 空（**他のアドレスへは送らない**）
  - Slack チャンネル ID: CP-B3 の値
  - 除外キーワード: `["デモ"]`、カメラ扱いフォーマット名: 空
  - 自動実行: **無効のまま**（前提と判断 12）
  - 単価表の初版: `effective_from: 2025-09-01`、設計 2.3 の #2001 の値
- [ ] 保存が成功し、設定画面の変更履歴（`GET /audits`。**`admin` のみ**）に自分のメールアドレスと変更前後が記録されることを確認する。一般ユーザーで開くと変更履歴ブロックが出ない（403）ことも確認する。
- [ ] `targets` の表示が `{verification: true, production: false}` になっていること、接続先ホストが `verify.saas-a.example.net` で**オレンジのバッジ**が出ることを確認する。
- [ ] `slack_mode` の表示が `bot` か `webhook` か確認し、`webhook` なら「チャンネル ID が無視される」注意書きが出ることを確認する。
- [ ] `target_env` を `production` に変えて保存を試み、**400 `TARGET_ENV_NOT_CONFIGURED`** で拒否されることを確認する（設計 3.3）。
- [ ] コミットは不要。

**受け入れ条件:** 設定と単価表が登録され、監査履歴が残る。未登録環境への切替が拒否される。環境バッジが出る。

---

### Task G-3: 検証環境で手動実行（配布なし）と Excel の確認

**目的:** 設計 14.1 の #7〜#9 を実施する。

**手順:**
- [ ] 画面の手動実行から `{month: "2026-07", target_env: "verification", distribute: false, force: false}` を実行する（**課金対象期間が完了している過去月**を選ぶ）。
- [ ] ステートマシンの実行を追う: `aws stepfunctions describe-execution --execution-arn <arn>` が `SUCCEEDED`。失敗したら実行履歴で失敗ステートを特定して原因を報告する。
- [ ] **所要時間を記録する:** RUN の `metrics.duration_sec` と CloudWatch メトリクス `TenantDuration`。**1 テナント 15 分予算に対する余裕を報告する**（設計 6.10）。
- [ ] 画面の月次内訳が表示され、テナント → 現場 → 地点のツリーと色分けが出ることを確認する。
- [ ] Excel をダウンロードし、**目視で以下を確認する**（設計 6.6）:
  - シートが 2 枚（`請求照合` / `全地点（管理用）`）
  - 両シート先頭に処理仕様ブロック（集計対象が「検証環境（verify.saas-a...）」）
  - シート1 に課金地点のみ、合計・保存期間別内訳・カメラ内訳
  - シート2 に全地点、オートフィルタ付きのテーブル、色分け
  - フォントが Meiryo、**中華フォントで表示されていない**
  - 金額が数値型で `¥` 表記
- [ ] **要確認 #5（テーブルとサマリの共存）**の結果を報告する。
- [ ] **連続する 2 か月を実行して削除地点差分を確認する**（設計 14.1 の #9）: 続けて `2026-08` を `distribute: false` で実行し、消えた地点があればシート2 に灰字で出て金額に影響しないことを確認する。
- [ ] **現行スキルとの突合**（設計 14.1 の #8）: `saas-a-billing-report` スキルで同じ期間を集計し、地点単位で比較する。差異が設計 6.5 の移植点 3 つ（除外対象の限定・通信なし 0 円・単価表の版）で説明できることを確認し、**説明できない差異があれば報告する**。
- [ ] `distribute: false` なので通知が飛んでいない（SQS が空、RUN が `succeeded` 止まり）ことを確認する。
- [ ] コミットは不要（コード修正が必要になった場合のみ修正してコミットする）。

**受け入れ条件:** ステートマシンが成功し、Excel が 2 シート・正しい色・Meiryo で生成される。削除地点差分が動く。現行スキルとの差異が移植点で説明できる。所要時間が報告されている。

---

### Task G-4: 配布テスト（メール + Slack）と E2E

**目的:** 設計 14.1 の #10 を実施し、E2E を 1 本通す（ADD-APP-GUIDE ステップ 8）。

**Files:** Create `frontend/e2e/saas-a-billing.spec.ts`

**流用元:** プラットフォーム計画 Task P4-6 の `frontend/e2e/stubs/auth.ts`（`seedAmplifyAuth()` / `stubCognitoEndpoints()`）と `frontend/e2e/fixtures/auth.fixture.ts`（ロール別の認証済み Page fixture）。

**手順:**
- [ ] **CP-B4 の確認:** 配布先が `devA@example.co.jp`（To）のみ、Slack がテスト用または CP-B3 のチャンネルであることを設定画面で再確認する。**ユーザーに投稿先を提示して確認を取ってから実行する。**
- [ ] 画面から `{month: "2026-07", target_env: "verification", distribute: true, force: true}` を実行する。
- [ ] **メールを確認する:**
  - 件名が `【SaaS_A】月額ライセンス料金一覧 2026年07月分（07/20締め） [検証環境]`（**`[検証環境]` が付く**）、`attempt > 1` なので「再送（第 n 回）」も付く
  - 本文にサマリ 9 項目・課金対象期間・使用単価表・ポータル URL
  - 添付が `saas_a_billing_2026-07_verification.xlsx`（**`_verification` が付く**）で開ける
- [ ] **Slack を確認する:**
  - 本文先頭に `【検証環境】`
  - サマリとポータル URL（**Excel は添付されていない**）
  - メンション設定があれば先頭に付く
  - Webhook フォールバック中ならチャンネル ID が無視されて Webhook のチャンネルに出る（設計 8.5）
- [ ] RUN が `distributed` になり、`notify_message_id` / `notify_channels` が記録されることを確認する。
- [ ] **`slack_channel_id` を空にして再実行**し、`channels` が `["email"]` になり `distribute_note: "Slack 未設定"` が画面と実行履歴に出ることを確認する（設計 6.8）。**要確認 #6 の結果を報告する。** 確認後は設定を戻す。
- [ ] **E2E を書いて通す:** `frontend/e2e/saas-a-billing.spec.ts` — ログイン（`seedAmplifyAuth` で認証済み状態を作る）→ サイドバーから `/saas-a-billing` → 実行履歴一覧が表示される → 行をクリックして月次内訳が開く → 設定画面が開く（`admin` fixture）。`E2E_BASE_URL=https://backoffice-dev.example-cloud.com npx playwright test e2e/saas-a-billing.spec.ts`。
- [ ] `.playwright-mcp/` 以外の場所にスクリーンショットを散らかさない。
- [ ] CRLF 変換・確認 → コミット（`mod refs #1001 saas-a-billing の E2E テストを追加`）。`git status --short` で差分ゼロを確認。

**受け入れ条件:** `devA@example.co.jp` に添付付きメールが届き、件名に `[検証環境]`、添付名に `_verification` が付く。Slack に `【検証環境】` 付きで投稿され Excel は添付されない。`slack_channel_id` 空でメールのみ配布される。E2E が dev で 1 本通る。

---

## Self-Review（計画作成者による確認）

**設計書のカバー状況:**

| 設計章 | 対応タスク |
| --- | --- |
| 2.1 締め日時と課金対象期間 | A-2 |
| 2.2 課金判定 / 2.5 差異要因 | A-4 |
| 2.3 単価表 | A-3 |
| 2.4 カメラ判定 | A-4（設定側）/ A-5（export 側） |
| 3.1 月次自動実行 / 6.2 実行判定 | D-1 |
| 3.2 手動実行 | E-2（`POST /runs`）/ F-4（ダイアログ） |
| 3.3 設定画面 | B-2（既定値の seed）/ E-2（検証）/ F-4（UI） |
| 3.4 レポート閲覧 | F-2 / F-3 |
| 3.5・6.6 Excel | C-1 / C-2 / G-3（目視） |
| 3.6・6.8 通知 | A-6（文面）/ D-4（投函）/ G-4（実測） |
| 4 章 SaaS_A API | B-1 |
| 5.1 スタックと配置 / 5.4 SSM 契約 | E-3 |
| 5.2・6.3 ステートマシン | E-1 / E-3 |
| 6.4 テナント別収集 / CSV | A-5 / B-1 / D-3 |
| 6.5 判定・金額 | A-4 |
| 6.7 S3 / 7.2 レイアウト | B-3 |
| 6.9 RUN の状態遷移 | B-2 |
| 6.10 所要時間 | D-3（時間予算）/ G-3（実測） |
| 7.1 DynamoDB | B-2 / E-3 |
| 7.3 result.json | D-4 |
| 8 章 API | E-2（+ 設計 8 章に無い `GET /audits`。前提と判断 13） |
| 9 章 画面 | F-1〜F-4 |
| 10 章 設定と保管先 | E-3（環境変数）/ B-2（DynamoDB）/ CP-B2（SSM） |
| 11 章 エラー・冪等性・再実行 | B-2 / D-1〜D-5 |
| 12 章 セキュリティ | E-2（認可）/ E-3（IAM）/ 各タスクの「秘密がログに出ない」テスト |
| 13 章 テスト方針 | 全タスクが TDD。カバレッジ 80% は D-5 と C-2 で確認 |
| 14.1 初回導入手順 | G-1〜G-4（#1〜#10 に対応。#11〜#16 は prod なので範囲外） |
| 14.2 監視 | E-3（アラーム・メトリクス）/ D-3・D-4（`put_metric`） |
| ADD-APP-GUIDE 4 章 チェックリスト | 各タスクの受け入れ条件に分散（IAM / SSM 契約 / 認証認可 / タグ命名 / ログ運用 / フロント / テスト文書） |

**本計画で扱わないもの:** 本番 API（Bearer 版）への切替（設計 14.4。本番 API 未リリース）、prod へのデプロイ（設計 14.1 の #11〜#16）、自動実行の有効化（設計 14.1 の #15）、テナント単位の再実行（設計 11.3 で「設けない」と決定済み）。

**インターフェースの一貫性:**
- `ClosingPeriod`（A-2）は A-4 / A-5 / A-6 / B-1 / D-2 / D-3 / D-4 が共通で使う。`start` / `end_exclusive` が半開区間、表示は `display_end`、締め日は `cutoff_date`、取得最終日は `last_date`。
- `PriceTable` / `Band`（A-3）は A-4 / B-2 / C-1 / C-2 / D-2 / D-4 が使う。
- `ExportResult`（A-5）は B-1 の `collect_export` が返し、D-3 が parts に書く。
- `DeviceJudgement`（A-4）のフィールド名は `result.json` の `devices[]`（設計 7.3）と 1:1 対応させ、C-1 / C-2 / F-3 がそのまま読む。
- `repo` の RUN 更新関数（B-2）は D-1〜D-5 と E-2 が使い、条件式は設計 6.9 の表と一致。
- `messages.attachment_filename()`（A-6）は `storage.xlsx_key()`（B-3）と D-4 の `notify.Attachment(key=..., filename=...)` が同じ値を使う（`put_attachment()` は使わない。前提と判断 18）。
- フロントの `types.ts`（F-1）は shared `response.py` のエンベロープと設計 7.3 の `result.json` に対応。
