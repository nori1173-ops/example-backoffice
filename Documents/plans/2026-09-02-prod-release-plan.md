# 本番リリース前ブラッシュアップ計画（#1005）

- 日付: 2026-09-02 / ブランチ `feature/#1005`（develop から分岐）/ 親チケット #1001
- 入力: レビュー 3 本（`.superpowers/sdd/2026-09-02-prod-release/findings-{spec,security,ui}.md`。指摘 ID = SP-xx / SEC-xx / UI-xx）
- 台帳: `.superpowers/sdd/2026-09-02-prod-release/progress.md`
- ゴール: 指摘の CRITICAL/HIGH をすべて解消し、安価な MEDIUM を同梱して dev で検証 → develop へマージ → master へマージ → prod スタック新規作成 → 疎通確認

## Global Constraints（全タスク共通）

1. 既存ファイルの改行コード（このリポジトリはほぼ CRLF。`*.sh` は LF）・BOM・文字コードを変えない。新規ファイルは UTF-8・CRLF（`*.sh` は LF）・末尾改行あり。編集前後に `file` / `grep -c $'\r'` で確認する
2. TDD: 先にテストを書いて失敗を確認 → 実装 → 緑。バックエンドは `uv run pytest -q`（リポ直下）、フロントは `cd frontend && npm test && npm run lint && npm run build`、edge は `node --test "infra/edge/functions/*.test.mjs"`、テンプレートは `bash scripts/validate-templates.sh`
3. 秘密値（トークン・パスワード・SecureString の値）をコード・テスト・ログ・コミット・チャットに書かない
4. タスクごとにコミット（1 タスク 1〜3 コミット）。件名 `mod refs #1005 <要点>`（バグ修正は `fix refs #1005`）、80 字程度、本文は箇条書き 3〜5 行まで。日本語はリポ外の UTF-8 一時ファイルに書いて `git commit -F`
5. デプロイしない（dev への反映はメインが行う）。AWS への書き込みをしない。サブエージェントを起動しない。自分のタスクのファイル一覧の外を触らない（気づいた点は報告に書く）
6. 自明なコメントを書かない。不要な空白を残さない。設計判断で迷ったら報告に「要判断」として書き、最も安全な側で実装して進める（止まらない）

## 決定事項（Rulings）

- R1: 集計対象環境はサイトごとに固定（案 C）。`apps/saas-a-billing/template.yaml` にパラメータ `TargetEnv`（AllowedValues: verification / production）を追加し、samconfig で dev=verification / prod=production。画面の環境選択は撤去し、**ラベルは維持**（環境バッジ、件名 `[検証環境]`、Excel ファイル名、S3 プレフィックス、RUN レコードの `target_env`）。SSM は固定環境の 3 本だけ読む。dev の `targets/production/*` 仮登録はメインが削除する
- R2: MFA は `ON`（必須）。dev / prod とも。フロントはサインイン時の `CONTINUE_SIGN_IN_WITH_TOTP_SETUP` を既に処理している（`stores/auth.ts`）
- R3: Cognito のメールは SES（DEVELOPER）に切替。送信元 `noreply@{MailDomain}`（core の SES Identity を利用）
- R4: CSP は edge の ResponseHeadersPolicy で入れる。connect-src は self + `https://cognito-idp.ap-northeast-1.amazonaws.com` のみ。Excel ダウンロードは fetch/XHR を使わず遷移（anchor / location）で行い、S3 ホストを CSP に足さない
- R5: Slack は prod も bot モード（dev と同じ Bot token を SSM へコピー）。shared samconfig `[production]` を `SlackMode=bot` にする
- R6: `POST /runs` は設計 12.1 どおり認証済み全ユーザーが実行可。ただし `distribute` の既定は false にし、配布 ON のときは画面で明示確認（チェックボックス）を必須にする
- R7: サイト環境ラベルはビルド変数 `VITE_SITE_ENV`（`dev` / `prod`、未設定は `dev` 扱い）。`scripts/deploy-frontend.sh` が渡す
- R8: 手動実行の監査（SEC-10）は RUN レコードに `requested_by` / `requested_at` の履歴（直近 20 件）を残す。30 分で終わらなければ park して報告

## タスク

### レーン A（バックエンド・インフラ）— 順に 1 エージェント

#### A1: 集計対象環境の固定（R1）— SP-01, SEC-08, UI-08 の根本対応
- 対象: `apps/saas-a-billing/template.yaml`, `apps/saas-a-billing/samconfig.toml`, `functions/api/app.py`, `functions/batch/handlers/{trigger,init,aggregate,mark_failed}.py`（`settings["target_env"]` を読む箇所）, `functions/saas_a_billing/{repo,saasa_client,messages}.py`（必要箇所のみ）, `apps/saas-a-billing/tests/**`
- 仕様:
  - `TargetEnv` パラメータ → 全 Lambda の環境変数 `TARGET_ENV`。samconfig dev=verification / prod=production
  - settings の `target_env` は環境変数の値で固定: `GET /settings` は固定値を返す（seed も固定値）。`PUT /settings` で異なる値が来たら 400（エンベロープ、`code: TARGET_ENV_FIXED` 等）。既存レコードの値が固定値と違っても読み出しで固定値に上書き
  - `POST /runs` の `target_env` は省略可。指定され固定値と異なれば 400。実行入力は固定値
  - trigger / init / aggregate / mark_failed は settings ではなく `TARGET_ENV` を使う（settings 経由でも同値になるが、環境変数を正とする）
  - SSM は `targets/{TARGET_ENV}/{base-url,user-id,password}` のみ参照。IAM の ssm:GetParameter も固定環境のパスに絞る（`!Sub` で `${TargetEnv}` を使う）
  - テスト: 固定値の返却 / PUT 拒否 / POST の不一致 400 / trigger が環境変数を使う / IAM リソースの形（template のテスト方針が既にあればそれに倣う）
- 完了条件: pytest 全緑、`bash scripts/validate-templates.sh` 緑

#### A2: バックエンドのセキュリティ・仕様修正
- 対象: `functions/saas_a_billing/excel.py`（SEC-01: `xlsxwriter.Workbook(..., {"strings_to_formulas": False, "strings_to_urls": False}`）, `shared/layers/example_backoffice/origin_verify.py` + `shared/tests`（SEC-03/SP-08: `ORIGIN_VERIFY_SKIP` は `ENV` が `prod` のとき無視。`ENV` 未設定も無視＝fail-closed）, `functions/saas_a_billing/saasa_client.py`（SEC-06: base-url は `https://` 以外を拒否）, `infra/core/src/notifier/*` + `infra/core/tests`（SEC-07: 宛先は `@example.co.jp` のみ・添付キーは `AttachmentsBucket` 配下の想定プレフィックスのみ。設計書 7 章の契約に合わせる）, `shared/layers/example_backoffice/auth.py`（SEC-12: `token_use == "id"` を検証）, `infra/core/src/pre_signup/app.py`（SEC-13: `@` が 1 個・小文字化・前後空白除去・完全一致）, `functions/api/app.py`（SP-07: `distribute` 既定 false。SEC-10: R8 の監査履歴）, `functions/saas_a_billing/repo.py`（SEC-10 の保存）, `Documents/images/gen_architecture.py`（SP-10: E501 と format）
- 完了条件: pytest 全緑、`make lint` 緑、`node --test` 緑

#### A3: インフラ・スクリプト
- 対象: `infra/core/template.yaml`（R2: `MfaConfiguration: ON` / R3: `EmailConfiguration` DEVELOPER + SES Identity ARN + From `noreply@{MailDomain}`（SES Identity と同一リージョン。依存順に注意）/ SP-02: `OpsTopic` の ARN を SSM `/{ProjectName}/{Env}/ops/topic-arn` に公開）, `apps/saas-a-billing/template.yaml`（SP-02: パラメータ `OpsTopicArn`（SSM 型）を 4 本のアラームの `AlarmActions`/`OKActions` に接続 / SP-04: `Period` 86400→300、`EvaluationPeriods` 1、`TreatMissingData: notBreaching` / SEC-05: HttpApi の `DefaultRouteSettings` に `ThrottlingBurstLimit: 20`, `ThrottlingRateLimit: 10` / SEC-09: 成果物バケットに `VersioningConfiguration: Enabled` + NoncurrentVersionExpiration 30 日 / SP-13: `parts/` と `tenants.json` の Expiration 90 日）, `apps/saas-a-billing/samconfig.toml`（`OpsTopicArn=/example-backoffice/{env}/ops/topic-arn`）, `infra/edge/template.yaml`（SEC-04: ResponseHeadersPolicy に CSP。R4 の値。既存の HSTS 等は維持）, `shared/samconfig.toml`（R5）, `scripts/deploy-frontend.sh`（R7: `VITE_SITE_ENV="${ENV}"` を build に渡す）, `scripts/deploy-edge.sh`（SEC-15: 秘密を argv に載せず環境変数/`--parameter-overrides` のファイル経由にし、`sam deploy` 出力に秘密が出ないようにする）, 新規 `scripts/add-admin.sh {env} {email}`（SP-06: SSM の user-pool-id を引いて `admin-add-user-to-group`。LF）, `scripts/README.md` と `README.md` の初回 prod 手順（SP-03: bot-token 登録 → shared deploy の順を明記）, 新規 `tests/test_samconfig.py`（SP-09: 各 samconfig の `[production]` が `/prod/` キー・`Env=prod`・`LogLevel=INFO`・`confirm_changeset=true`・prod ドメインを持つこと、`[default]` が dev であること）, `templates/app/**` の雛形（`OpsTopicArn` と `TargetEnv` 相当は雛形には不要。アラームを持つならパラメータだけ追随）, `Documents/ADD-APP-GUIDE.md`（OpsTopicArn の追記 1 箇所）
- 完了条件: `bash scripts/validate-templates.sh` 緑（cfn-lint 含む）、`uv run pytest -q` 緑、shellcheck 相当の目視

### レーン B（フロントエンド）— 順に 1 エージェント

#### B1: 集計対象環境固定の UI とサイト環境ラベル（R1, R7, UI-08, UI-12）
- 対象: `frontend/src/apps/saas-a-billing/{components/RunDialog.vue,views/SettingsView.vue,views/RunListView.vue,stores/settings.ts,stores/runs.ts,api.ts,types.ts,__tests__/**}`, `frontend/src/layouts/ShellLayout.vue`, `frontend/src/env.d.ts`, `frontend/index.html`（title）
- 仕様: RunDialog の環境選択を撤去し、固定環境（settings の `target_env`）を大きめのチップで表示（検証=オレンジ「検証環境の SaaS_A を集計」/ 本番=緑「本番 SaaS_A を集計（実データ）」）。SettingsView は環境を表示のみ（「このサイトでは固定」の注記）。RunListView の環境フィルタは撤去（バッジは維持）。POST /runs の `target_env` を送らない。ShellLayout のアプリバーに `VITE_SITE_ENV` に応じたチップ（dev=「検証環境」オレンジ / prod=「本番」緑）を常時表示し、`document.title` にも `[検証]` を前置。既存テストを新仕様に更新
- 完了条件: `npm test` / `npm run lint` / `npm run build` 緑

#### B2: 認証フローの UX（UI-05, UI-06, UI-23, UI-14）
- 対象: `frontend/src/views/{LoginView,SignupView,MfaSetupView}.vue`, 新規 `frontend/src/views/ForgotPasswordView.vue`, `frontend/src/components/TotpEnroll.vue`, `frontend/src/stores/auth.ts`, `frontend/src/router/index.ts`, `frontend/index.html`（favicon/meta。favicon は `frontend/public/favicon.svg` を新規作成: 単色の簡素なアイコン）, `frontend/src/test/**` / `__tests__`
- 仕様: パスワードリセット（`resetPassword` → コード + 新パスワードで `confirmResetPassword`、Login に導線）、確認コード再送（Signup の確認ステップと、Login で `UserNotConfirmedException` のときに再送導線）、MFA 画面は `fetchMFAPreference` で現在状態を表示し「再登録」導線にする、Cognito 例外の日本語化辞書を拡充（未知の例外は汎用文言）。二重送信防止・ローディングは既存パターンに倣う
- 完了条件: `npm test` / `npm run lint` / `npm run build` 緑

#### B3: アプリ画面のブロッカーと磨き（UI-01, 02, 03, 04, 07, 09, 10, 11, 13, 15, 16, 17, 19, 20, 22 + R6）
- 対象: `frontend/src/layouts/ShellLayout.vue`, `frontend/src/stores/ui.ts`, `frontend/src/apps/saas-a-billing/**`, 新規 `frontend/src/utils/format.ts`
- 仕様（要点。詳細は findings-ui.md の各 ID）: UI-01 `keep-alive` に `:key="route.fullPath"` または `onActivated` + params watch / UI-02 ドロワー既定は `useDisplay().mdAndUp` で決め、モバイルは temporary で閉じた状態から / UI-03 表を横スクロール可能なコンテナに入れ、列に優先度を付けて `lg` 未満で副次列を隠す。合計金額・ダウンロード・判定・金額は必ず初見で見える / UI-04 一覧は `onActivated` で再取得 + 更新ボタン + 実行中（RUNNING）の間は 10 秒ポーリング / UI-07 + R6 再実行は「既存の結果を上書きする」旨の警告、`force` は明示チェック、配布 ON のときは実際の宛先（メール宛先・Slack チャンネル）を表示して確認チェック必須 / UI-09 未保存離脱ガード（`onBeforeRouteLeave` + `beforeunload`） / UI-10 保存結果はスナックバーに統一 / UI-11 combobox の未確定入力は blur/Enter で確定 / UI-13, 16 日時は JST `YYYY-MM-DD HH:mm`、金額は `¥1,234,567` に統一（format.ts） / UI-15 ダウンロードは anchor 遷移 + 進行表示 + 無効理由 / UI-17 履歴の変更項目に表示名マップ / UI-19 非 admin は読み取り専用の見た目（disabled + 注記） / UI-20 判定の凡例 / UI-22 内訳に「実行履歴へ戻る」
- 完了条件: `npm test` / `npm run lint` / `npm run build` 緑。既存テストは仕様変更分のみ更新

### レーン C（コード完了後）

#### C1: 設計書の追随
- 対象: `Documents/DESIGN.md`（MFA ON、Cognito メール SES、CSP、SSM `ops/topic-arn`、`VITE_SITE_ENV`、add-admin runbook、初回 prod 手順、ORIGIN_VERIFY_SKIP の prod ガード、token_use）, `Documents/apps/saas-a-billing/DESIGN.md`（固定 target_env: 2 章・5 章・7.1・8 章・API 表、`distribute` 既定、アラーム、監査履歴、保持方針）, `Documents/ADD-APP-GUIDE.md`, `Documents/COST-ESTIMATE.md`（SP-11 アラーム個数）, `README.md`, `apps/saas-a-billing/README.md`
- HTML/PDF の再生成はメインが md-to-design-doc-html で実施

### 最終工程（メイン）
最終ブランチレビュー（Opus）→ dev 全スタックデプロイ（shared → core → app → edge → frontend）→ dev の `targets/production/*` 削除 → Playwright MCP で画面確認 → develop へマージ・push → develop → master マージ・push → prod SSM 登録 → prod デプロイ → 疎通確認 → チケット・メモリ更新

## 見送り（次チケットへ）
- SEC-11（requiredGroups の親子 OR は設計どおり）、SEC-14（presign 300 秒で可）、UI-18 / 21 / 24 ほか LOW 全部、SP-12（8 章のエラー形式は C1 で文言だけ直す）
