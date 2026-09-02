# example-backoffice プラットフォーム基盤 実装計画（dev-tracker #1002）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 社内保守管理サイト example-backoffice の共通基盤（monorepo 骨格 / shared レイヤ / core / edge / frontend シェル）を dev 環境で動く状態まで作る。

**Architecture:** CloudFront 1 本の背後に S3(SPA) と各アプリの HTTP API を同居させる。Cognito User Pool（TOTP MFA、`@example.co.jp` 限定セルフサインアップ）で認証し、CloudFront Function（viewer-request）の IP allowlist で社内に閉じる。メールと Slack の送信は core の SQS → notifier Lambda に集約し、アプリは shared レイヤの `notify.send()` を呼ぶだけにする。スタック間の受け渡しは SSM Parameter Store `/example-backoffice/{env}/...` を契約レジストリとする。

**Tech Stack:** AWS SAM / CloudFormation、Lambda（Python 3.13）、Powertools for AWS Lambda (Python)、社内レイヤ ExamplePowertoolsPython、API Gateway HTTP API + Cognito JWT authorizer、DynamoDB、S3、SQS、SES v2、CloudFront、Route53、Vue 3.5 / Vuetify 4 / Vue Router 5 / Pinia 4 / Vite 8 / TypeScript 6 / Amplify v6、pytest / moto / responses、Vitest / @vue/test-utils、Playwright。

**Spec:**
- `Documents/DESIGN.md`（プラットフォーム設計書。SSM キー一覧の正本）
- `Documents/ADD-APP-GUIDE.md`（アプリ追加手順・レビューチェックリスト）
- `Documents/apps/saas-a-billing/DESIGN.md`（第一弾アプリ設計書。基盤が満たすべき要件の実例）
- `README.md`（ディレクトリ構成・デプロイ順）

**関連計画:** `Documents/plans/2026-08-28-saas-a-billing-plan.md`（dev-tracker #1003）。本計画の Phase 5 完了が前提。

---

## Global Constraints

以下は全タスクの要件に暗黙に含まれる。

### AWS / 環境

- AWS アカウント `210000000001`、リージョン `ap-northeast-1`（**全スタック**。us-east-1 にスタックは作らない。ACM 証明書だけは us-east-1 の既存証明書を参照する）。`AWS_PROFILE` は環境変数（`default`）で指定し、`samconfig.toml` に書かない。
- `env` ∈ {`dev`, `prod`}。本計画で実際にデプロイするのは **dev のみ**。prod 用の `samconfig` は書くがデプロイしない。
- **共有リソースは参照・新規追加のみ。他プロジェクトのリソースを変更・削除しない。**
  - Route53 ホストゾーン `Z0EXAMPLEZONE0`（`example-cloud.com`）
  - ACM 証明書 `arn:aws:acm:us-east-1:210000000001:certificate/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee`（`*.example-cloud.com`）
  - SES 東京リージョンの送信枠（本番アクセス済み）
  - SAM のアーティファクトバケット `example-sam-ap-northeast-1`
  - 社内レイヤ `arn:aws:lambda:ap-northeast-1:210000000001:layer:ExamplePowertoolsPython:86`
- ドメイン: dev `backoffice-dev.example-cloud.com` / prod `backoffice.example-cloud.com`。
- 破壊的操作（`aws ... delete-*`、`sam delete`、`cloudformation delete-stack`、`rm -rf`、`git reset --hard`）は実行前にユーザー確認を取る。

### ローカルツールチェーン（実測値）

| ツール | 版 | 制約 |
| --- | --- | --- |
| Node.js / npm | 24.13.0 / 11.6.2 | そのまま使う |
| Python | **3.12.3 のみ**（3.13 は無い） | Lambda ランタイムは 3.13。`sam build --use-container` を必須とする。ユニットテストは 3.12 で走らせるため **3.13 限定構文を使わない**（PEP 695 の `type` 文、`from typing import override` 等） |
| SAM CLI | 1.154.0 | |
| cfn-lint | 1.43.4 → **1.51.3 以上へ更新**（Task P0-3） | |
| Docker | 29.1.3（稼働中） | `sam build --use-container` に使う |
| uv | 0.10.8 | Python 依存管理はすべて uv |
| pytest | 9 | |

### ファイル規約

- 新規ファイルは **UTF-8 / CRLF / BOM なし**。ただし `.sh` のみ **LF**。
- Write / Edit ツールは LF で書き込むため、**新規作成した非 `.sh` ファイルは作成後に CRLF 変換する**。既に CRLF のファイルには適用しない（二重適用で `\r\r\n` になる）。

```bash
# CRLF 変換（新規作成した非 .sh ファイルのみ。既に CRLF のものには実行しない）
sed -i 's/$/\r/' <path>
file <path>            # → "with CRLF line terminators" を確認
```

```bash
# 変換前の状態確認（\r\r\n の事故を防ぐ）
file <path>            # "with CRLF line terminators" と出たら sed は不要
```

- 新規ファイルは末尾に改行を付ける。自明なコメントは書かない。
- 一時ファイルはリポジトリ内に作らない（`/tmp/` を使う）。
- **`Documents/DESIGN.md` / `Documents/ADD-APP-GUIDE.md` / `Documents/apps/saas-a-billing/DESIGN.md` / `README.md` を編集しない。** 設計と実装が食い違う場合は実装を設計に合わせ、どうしても設計側が誤っている場合は「要確認」として報告のみ行う。

### タグ（設計 14.2。CloudFormation テンプレートのタスクの完了条件）

- **スタック全体**（`samconfig.toml` の `tags`）: `Project=example-backoffice`、`App`（`core` / `shared` / `edge` / アプリ id）、`Env`（`dev` / `prod`）。
- **タグ対応リソース全部**（テンプレート内の `Tags`）: `Project`（`!Ref ProjectName`）と `Name`（役割）。`Name` の許容値は `Function` / `Api` / `Bucket` / `Distribution` / `Queue` / `Table` / `LogGroup` / `Role` / `UserPool` / `StateMachine` / `Schedule`（設計 14.2）に、本計画で実際に使う **`Topic`（`AWS::SNS::Topic`）/ `Alarm`（`AWS::CloudWatch::Alarm`）/ `ConfigurationSet`（`AWS::SES::ConfigurationSet`）/ `EventSourceMapping` / `EmailIdentity` / `EdgeFunction`（`AWS::CloudFront::Function`）** を加えたものとする（前提と判断 16）。**正本は `tests/infra/test_template_tags.py` の `ALLOWED_NAME_VALUES`**（Task P0-4 で `Layer` を削除し 3 値を追加済み。WAF 廃止に伴い `WebACL` も使わない）。
- **許容値の増やし方:** 設計 14.2 の 13 値にも上の 3 値にも当てはまらないタグ対応リソースが出たら、(1) `tests/infra/test_template_tags.py` の `ALLOWED_NAME_VALUES` に値を追加し、(2) その旨をタスク報告に書き、(3) **設計 14.2 の表への追記をユーザーに提案する**（設計書は編集しない）。勝手に `Name` を既存の値へ流用しない。
- 全テンプレートに `Parameters.ProjectName`（`Type: String`, `Default: example-backoffice`）を置く。

```yaml
Parameters:
  ProjectName:
    Type: String
    Default: example-backoffice
```

```yaml
      Tags:
        - Key: Project
          Value: !Ref ProjectName
        - Key: Name
          Value: Queue
```

- `AWS::Serverless::Function` / `AWS::Serverless::HttpApi` の `Tags` は **マップ形式**（`{ Project: !Ref ProjectName, Name: Function }`）である点に注意する。`AWS::S3::Bucket` / `AWS::SQS::Queue` / `AWS::Logs::LogGroup` などの素の CloudFormation リソースは **リスト形式**。
- **`AWS::Serverless::LayerVersion` に `Tags` プロパティは無い。** タグを書くと `sam validate --lint` が落ちるため、タグ非対応型として扱う（`UNTAGGABLE_TYPES` に入れる。Task P0-4）。
- CloudFront `AWS::CloudFront::Distribution` はリスト形式（`Tags` はトップレベル、`DistributionConfig` の外）。
- タグ非対応リソース（`AWS::SSM::Parameter` は対応、`AWS::CloudFront::OriginAccessControl` / `AWS::Route53::RecordSet` / `AWS::S3::BucketPolicy` は非対応）にはタグを付けない。**`AWS::CloudFront::Function` は `Tags` に対応する**（`Name: EdgeFunction`。Task P0-4 で確認済み）。判断に迷ったら `sam validate --lint` と `cfn-lint` の結果に従う。

### コミット

各タスクの最後に必ずコミットする（push はしない。push はメインエージェントが行う）。

```bash
cd /mnt/c/Users/devA/git/example-backoffice
git add <変更したファイル>
printf 'mod refs #1001 <要点>\n' > /tmp/commit_message.txt
git commit -F /tmp/commit_message.txt
rm -f /tmp/commit_message.txt
git status --short          # 差分ゼロを確認
```

- 日本語を含むメッセージは必ず `/tmp` 配下の UTF-8 一時ファイル経由で `git commit -F` する（文字化け防止）。
- ブランチは `feature/#1001`。件名は 80 字程度まで。設計判断の経緯はコミットに書かない。

### 検証コマンド（該当するものを各タスクで実行する）

```bash
cd /mnt/c/Users/devA/git/example-backoffice
uv run pytest <対象> -v                       # Python ユニットテスト
uv run pytest --cov=<pkg> --cov-report=term-missing --cov-fail-under=80
uv run ruff check . && uv run ruff format --check .
cd <テンプレートのあるディレクトリ> && sam validate --lint
cd frontend && npm test                        # Vitest
cd frontend && npm run lint
cd frontend && npm run build                   # vue-tsc -b && vite build
```

---

## 前提と判断

設計書に書かれていない判断は以下のとおり決めた。業務ルール・データ設計は変更していない。

1. **実装順は shared → core とする。** 設計 3.3 のデプロイ順（core → shared → app → edge → frontend）は守るが、コードを書く順は逆にする。core の notifier が `shared/schemas/notify.v1.json` を正本として参照するため、スキーマを先に確定させる必要がある。
2. **`notify.send()` の実行時検証は自前の軽量バリデータで行う。** 設計 10.1 が「shared 自身は標準ライブラリと boto3 のみに依存する」と定めているため、`jsonschema` をレイヤに入れない。JSON Schema（`shared/schemas/notify.v1.json`）は正本として置き、**テストでのみ** `jsonschema` を使って「自前バリデータが通したメッセージが Schema にも適合すること」を両側（`notify.py` / notifier）で検証する。
3. **Python の依存管理は uv、設定はリポジトリルートの `pyproject.toml` に集約する。** テスト・lint 用の dev 依存（pytest, pytest-cov, moto, responses, jsonschema, boto3, aws-lambda-powertools, XlsxWriter, openpyxl, ruff）はここに書く。Lambda の実行時依存は各関数ディレクトリの `requirements.txt`（`sam build` 用）に別途書く。`requires-python = ">=3.12"`。
4. **`Fn::GetStackOutput` は使わない。** スタック間の契約はすべて SSM 型パラメータで受ける（WAF 廃止でリージョンをまたぐ値の受け渡しが無くなったため。前提と判断 18）。cfn-lint は将来の利用と新規則のために 1.51.3 以上へ更新する（Task P0-3）。
5. **`.gitattributes` の先頭行に `* -text` を置く（改行変換を一切しない = 作業ツリーのバイト列をそのまま保存）。** 既存の `*.sh text eol=lf` / `*.bat text eol=crlf` は**その後ろに残す**（後の行が優先されるため `.sh` は LF 固定のまま）。`* text=auto eol=crlf` は**使わない** — 既にコミット済みの CRLF ファイル（設計書など）が次回 `git add` の際に リポジトリ内で LF へ renormalize され、「既存ファイルの改行コードを変更しない」規約に反するため。`-text` なら既存ファイルのバイト列も、cp932 の CSV フィクスチャも壊れない。
6. **shared レイヤのランタイムは Python 3.13、`BuildMethod: python3.13`。** ローカルに 3.13 が無いため `sam build --use-container` を必須とする（`--use-container` なしでビルドすると 3.12 のバイトコードが混ざる恐れがある）。
7. **Slack の Bot token も Webhook URL も未登録の場合、notifier は恒久エラーとして扱う**（例外を投げず DLQ へ退避してログに残す）。設計 8.6 の「添付が S3 に無い」と同じ扱い。アプリ側は `slack_channel_id` 未設定なら `channels` から `slack` を外せるため、実運用でこの経路には落ちない。→ 要確認 #1。
8. **Cognito の `MfaConfiguration` は初期 `OPTIONAL`、`EnabledMfas: [SOFTWARE_TOKEN_MFA]`**（設計 6.1）。SMS 構成は作らない。`UserPoolTier: LITE` を指定する。
9. **CloudFront のログ（設計 12.4）は初期リリースで有効にする。** ログ用 S3 バケットを edge スタックに作り、ライフサイクル 365 日で削除する。`AWS::CloudFront::Distribution` の `Logging`（レガシー標準ログ）を使う（v2 標準ログは CloudFormation 非対応のため）。
10. **`templates/app/` の cookiecutter 雛形は Phase 4 で最小版を作る。** saas-a-billing は雛形から生成せず設計書どおり手書きし、課金アプリ完成後に雛形へ反映するかは別途判断する（本計画の範囲外）。
11. **frontend のパッケージ版は設計 9.2 の表をそのまま `package.json` に書く。** `npm install` で解決できない版があった場合は、その時点の最新安定版に落として **タスクの報告に必ず記載する**（設計書は編集しない）。→ 要確認 #4。
12. **`/health` も JWT 認証必須**（設計 12.3）。疎通確認では必ず ID トークンを付ける。
13. **疎通確認用の ID トークンは `pycognito`（SRP 認証）で取る。E2E 専用の App Client は追加しない。** 設計 6.1 の App Client は `ALLOW_USER_SRP_AUTH` + `ALLOW_REFRESH_TOKEN_AUTH` のみ・シークレット無しであり、`ADMIN_USER_PASSWORD_AUTH` を足すと設計から外れる。代わりに dev 依存に `pycognito` を入れ、`scripts/get_id_token.py`（Python）が SRP でサインインして ID トークンを標準出力に出す。TOTP MFA が有効なユーザーには `--totp-code` で対応する。Task P2-4 のテストは **SPA クライアント 1 本**（SRP + REFRESH のみ、`GenerateSecret: false`）を検査する。
14. **CloudFront の標準ログ用バケットだけは `ObjectOwnership: BucketOwnerPreferred`（ACL 有効）を許容する。** 設計 11.2 は「すべてのバケットで `OwnershipControls: BucketOwnerEnforced`」と定めているが、レガシー標準ログ（`DistributionConfig.Logging`）は配信先バケットの ACL 書き込みを要求するため `BucketOwnerEnforced` では配信できない。**この 1 バケットのみの例外**とし、SPA バケット・添付バケットは `BucketOwnerEnforced` のままにする。→ 設計 11.2 への例外追記をユーザーに提案する（設計書は編集しない）。
15. **（撤回）タグ `App` の許容値に `edge-global` を加える。** WAF 廃止で `edge-global` スタック自体が無くなったため不要（前提と判断 18）。**番号は後続の参照を壊さないため詰めない。**
16. **タグ `Name` の許容値に `Topic` / `Alarm` / `ConfigurationSet` を加える。** 設計 14.2 の 13 値に、core が実際に作る `AWS::SNS::Topic` / `AWS::CloudWatch::Alarm` / `AWS::SES::ConfigurationSet` の役割名が無いため。→ 設計 14.2 への追記をユーザーに提案する。
17. **`response.error()` のシグネチャは設計 10.2 どおり `error(code, message, status=400)` に揃える。** 状態コードを第 3 引数で受け、`json_response()` と組み合わせて使う。計画の初稿にあった `error(code, message)` は設計と食い違うため採らない。
18. **WAF を使わず、社内 IP 制限は CloudFront Function（viewer-request）で行う**（ユーザー決定 2026-08-28）。`infra/edge-global`（us-east-1 の `AWS::WAFv2::WebACL` + `IPSet`）は作らない。理由は費用（WAF は 2 環境で月約 $12、CloudFront Functions は実質 $0）。代償として WAF のサンプルリクエスト・ブロック件数メトリクス・WAF ログによる監査性と、IP set をデプロイ無しで即時更新できる運用性を失う（IP 追加は `infra/edge/samconfig.toml` の `AllowedIpAddresses` を直して `bash scripts/deploy-edge.sh {env}`、1〜2 分）。監査が必要になったら WebACL を作って `DistributionConfig.WebACLId` に付けるだけで戻せる。**CloudFront はビヘイビアごとに viewer-request 関数を 1 つしか関連付けられないため、IP 判定と SPA フォールバック（旧 Task P3-2）は同一関数 `infra/edge/functions/edge-guard.js` にまとめる。** 判定は IPv4 CIDR のみとし、`DistributionConfig.IPV6Enabled: false`・Route53 は A レコードのみにする（IPv6 経由の素通りを作らない）。→ 設計 5 章 / 7 章 / 16 章に反映済み。

### 要確認（実装中に判明したら報告する。勝手に設計を変えない）

| # | 事項 | 対応 |
| --- | --- | --- |
| 1 | Slack の bot-token / webhook-url が**両方**未登録のときの notifier の挙動が設計 8.6 に無い | 上記 7 の方針で実装し、ユーザーに報告する |
| 2 | `AWS::SES::EmailIdentity` の DKIM トークンから Route53 CNAME を作る書き方（`DkimDNSTokenName1..3` / `DkimDNSTokenValue1..3` の `GetAtt`）が ref-project-a の実装と一致するか | Task P2-5 で ref-project-a の `stacks/ses/template.yaml` を確認して合わせる |
| 3 | `AWS::Cognito::UserPool` の `UserPoolTier: LITE` が CloudFormation で受理されるか | Task P5-1 の dev デプロイで確認。拒否されたら省略して報告する |
| 4 | 設計 9.2 のフロント版（Vuetify `^4.1` / Vite `^8.2` / TypeScript `~6.0` / vue-router `^5.2` / Pinia `^4.0` / jsdom `^29`）が npm に実在するか | Task P0-2 の `npm install` で確認し、差異を報告する |
| 5 | `sam build --use-container` で `public.ecr.aws/sam/build-python3.13` が取得できるか | Task P1-5 の初回ビルドで確認 |
| 6 | HTTP API JWT authorizer が `cognito:groups` を `"[admin]"` 形式の文字列で渡すか配列で渡すか（設計 17 の #6） | `auth.py` は両形式を受理する実装にする。Task P5-3 の疎通で実測して報告する |

### ユーザー作業チェックポイント（サブエージェントは実施できない）

| # | いつ | 作業 | 影響するタスク |
| --- | --- | --- | --- |
| CP-1 | **Phase 5 の shared スタックのデプロイ後**（Task P5-2 の 1 手目のあと） | Slack App を作成しワークスペースにインストール、Bot に `chat:write` + `chat:write.public` を付与。`bash scripts/register-slack.sh dev bot` を実行し、**プロンプト（`read -rs`）に Bot token を貼る**（引数では渡さない。シェル履歴に残るため）。**すぐ用意できない場合は Incoming Webhook を作成して `bash scripts/register-slack.sh dev webhook` で登録する**（`mode` は `webhook` のまま） | P5-2, P5-3 |
| CP-2 | Phase 3 完了後 | `#proj-saas-a-billing` のチャンネル ID（`C...`）を Slack から取得してメインエージェントに伝える | saas-a-billing 計画の G-2 |
| CP-3 | Phase 5 の core デプロイ後 | dev サイトで管理者ユーザー（`@example.co.jp`）をサインアップし、Cognito コンソールで `admin` グループに追加する。E2E 用ユーザー（TOTP 未登録）も作る | P5-3, Phase 4 の E2E |
| CP-4 | Phase 5 | 社内 IP `203.0.113.10/32` 以外からのアクセスで 403 になることの確認（社外回線が必要な場合） | P5-3 |

**CP-1 を shared デプロイ前に実行してはいけない。** `/example-backoffice/{env}/shared/slack/mode` は shared スタックの `AWS::SSM::Parameter`（`SlackModeParameter`）が作る。先に `put-parameter` で同名パラメータを作ってしまうと、shared スタックの CREATE が「パラメータが既に存在する」で失敗する。`bot-token` / `webhook-url` は CloudFormation が作らないため先行登録しても問題ないが、手順を分けると事故るので **CP-1 はまとめて shared デプロイ後**に行う。

`register-slack.sh dev bot` は `/shared/slack/bot-token` を登録したうえで `/shared/slack/mode` を `aws ssm put-parameter --overwrite` で `bot` に更新する。**CloudFormation の管理下にある値を外から書き換えるため、`shared/samconfig.toml` の `parameter_overrides` の `SlackMode` も `bot` に直してコミットする**（次回の shared デプロイで `webhook` に戻るのを防ぐ）。

**メール送信テストは `devA@example.co.jp` 宛のみ。** Slack への初回投稿もテスト用チャンネル、または CP-2 で指定されたチャンネルに限る。

---

## File Structure

本計画で作成するファイル（フルパスは `/mnt/c/Users/devA/git/example-backoffice/` からの相対）。

```text
pyproject.toml                        Python dev 依存・pytest・ruff・coverage の設定
.gitattributes                        （追記）先頭行に `* -text`
.gitignore                            （追記）packaged.yaml / samconfig.local.toml のみ（既存行と重複させない）
Makefile                              test / lint / validate / build のショートカット

shared/
├── template.yaml                     LayerVersion / SSM layer-arn / SSM slack/mode
├── samconfig.toml                    [default]=dev, [prod]
├── schemas/notify.v1.json            通知メッセージスキーマ（正本）
├── layers/
│   ├── requirements.txt              （空。ContentUri 直下＝sam build が見る場所）
│   └── example_backoffice/
│       ├── __init__.py
│       ├── auth.py                   Claims / get_claims / require_group / is_admin
│       ├── origin_verify.py          x-origin-verify ミドルウェア
│       ├── notify.py                 send / put_attachment / Email / Attachment / Slack
│       ├── response.py               ok / error エンベロープ
│       └── logging.py                get_logger / LambdaLogger 再エクスポート
└── tests/
    ├── conftest.py
    ├── test_auth.py
    ├── test_origin_verify.py
    ├── test_notify.py
    ├── test_response.py
    └── test_schema_contract.py       自前バリデータ ↔ JSON Schema の一致

infra/core/
├── template.yaml                     Cognito / SES / SQS+DLQ / notifier / S3 / SSM / アラーム
├── samconfig.toml
├── src/
│   ├── pre_signup/
│   │   ├── app.py
│   │   └── requirements.txt
│   └── notifier/
│       ├── app.py                    SQS ハンドラ（Idempotency / ReportBatchItemFailures）
│       ├── ses_sender.py             SES v2 SendEmail（Simple + Attachments）
│       ├── slack_sender.py           chat.postMessage / Incoming Webhook
│       └── requirements.txt          boto3>=1.37.27
└── tests/
    ├── conftest.py
    ├── test_pre_signup.py
    ├── test_ses_sender.py
    ├── test_slack_sender.py
    └── test_notifier_handler.py

infra/edge/
├── template.yaml                     S3(SPA)+OAC / CloudFront / CFF / Route53 / ログ S3
├── samconfig.toml
├── functions/edge-guard.js           IP 許可リスト + SPA フォールバック（正本）
└── functions/edge-guard.test.mjs     node --test

frontend/
├── package.json / tsconfig*.json / vite.config.ts / vitest.config.ts
├── eslint.config.js / playwright.config.ts / index.html
├── e2e/auth.setup.ts
└── src/
    ├── main.ts / env.d.ts
    ├── config/amplify.ts
    ├── plugins/vuetify.ts
    ├── styles/settings.scss
    ├── router/{index.ts,guards.ts,guards.test.ts}
    ├── stores/{auth.ts,auth.test.ts,ui.ts}
    ├── services/{http.ts,http.test.ts}
    ├── layouts/{ShellLayout.vue,AuthLayout.vue}
    ├── components/{TotpEnroll.vue}
    ├── views/{LoginView,SignupView,MfaSetupView,HomeView,ForbiddenView,NotFoundView}.vue
    └── apps/
        ├── types.ts / registry.ts / registry.test.ts
        └── _template/{index.ts,routes.ts,api.ts,views/IndexView.vue,__tests__/index.test.ts}

templates/app/                        sam init --location 用 cookiecutter 雛形

scripts/
├── init-secrets.sh                   origin-verify-secret を SecureString で登録
├── register-slack.sh                 bot-token / webhook-url と mode を登録
├── deploy-edge.sh                    SSM の origin-verify を渡して edge を deploy
├── deploy-frontend.sh                SSM から Cognito ID 取得 → build → s3 sync → invalidation
├── deploy-all.sh                     core → shared → app → edge → frontend
├── get_id_token.py                   疎通確認用に ID トークンを取得（pycognito / SRP。.sh ではない）
├── list-mfa-status.sh                TOTP 未登録ユーザーの一覧
└── validate-templates.sh             全テンプレートに sam validate --lint
```

---

## Phase 0: リポジトリ基盤

### Task P0-1: monorepo ディレクトリ骨格と改行コード規約

**Files:**
- Modify: `/mnt/c/Users/devA/git/example-backoffice/.gitattributes`
- Modify: `/mnt/c/Users/devA/git/example-backoffice/.gitignore`
- Create: `/mnt/c/Users/devA/git/example-backoffice/Makefile`
- Create: `/mnt/c/Users/devA/git/example-backoffice/.gitkeep` 相当のディレクトリ（下記）

**流用元:** `/mnt/c/Users/devA/git/example-portal/.gitignore`（monorepo の除外パターン）。

**Interfaces:**
- Produces: 以降の全タスクが使うディレクトリ構造と `make` ターゲット。

- [ ] **Step 1: 現在の改行コードを確認する**

```bash
cd /mnt/c/Users/devA/git/example-backoffice
file .gitattributes .gitignore README.md
# 期待: いずれも "with CRLF line terminators"
```

- [ ] **Step 2: ディレクトリ骨格を作る**

```bash
cd /mnt/c/Users/devA/git/example-backoffice
mkdir -p shared/layers/example_backoffice shared/schemas shared/tests
mkdir -p infra/core/src/pre_signup infra/core/src/notifier infra/core/tests
mkdir -p infra/edge/functions
mkdir -p apps
mkdir -p frontend/src/{config,plugins,styles,router,stores,services,layouts,components,views,apps/_template/views,apps/_template/__tests__} frontend/e2e
mkdir -p templates/app
mkdir -p scripts
ls -d shared infra/core infra/edge apps frontend templates scripts
```

- [ ] **Step 3: `.gitattributes` の先頭行に `* -text` を足す**

既存の 2 行はそのまま残し、**先頭に 1 行だけ**足す。`-text` は「改行変換を一切しない」の意味で、作業ツリーのバイト列がそのままリポジトリに入る。**`* text=auto eol=crlf` は使ってはいけない** — 既にコミット済みの CRLF ファイル（設計書など）が次の `git add` で LF へ renormalize され、「既存ファイルの改行コードを変更しない」規約に反する（前提と判断 5）。**既存ファイル（CRLF）への追記なので `\r\n` で書く。**

```bash
cd /mnt/c/Users/devA/git/example-backoffice
printf '* -text\r\n' > /tmp/ga.txt
cat .gitattributes >> /tmp/ga.txt
cp /tmp/ga.txt .gitattributes
rm -f /tmp/ga.txt
cat -A .gitattributes | head -5
file .gitattributes
```

期待する `.gitattributes` の中身（**この 3 行だけ**。後の行が優先されるので `.sh` は LF、`.bat` は CRLF に固定される）:

```text
* -text
*.sh text eol=lf
*.bat text eol=crlf
```

- [ ] **Step 3b: 既存ファイルが renormalize されないことを確認する**

```bash
cd /mnt/c/Users/devA/git/example-backoffice
git add .gitattributes
git diff --cached --stat
# 期待: .gitattributes の 1 行追加のみ。Documents/*.md や README.md が差分に現れないこと
git status --short
```

`Documents/DESIGN.md` などが差分に現れた場合は `* -text` が効いていない。`git check-attr text -- Documents/DESIGN.md` が `text: unset` を返すことを確認する。

- [ ] **Step 4: `.gitignore` に追記する**

既存の CRLF を壊さないよう、追記も `\r\n` で行う。**既存の `.gitignore` には `node_modules/` `dist/` `__pycache__/` `.venv/` `.aws-sam/` `.env*` などが既にある。足すのは差分だけ**（`.venv/` を二重に書かない）。

```bash
cd /mnt/c/Users/devA/git/example-backoffice
grep -nx '\.venv/\|\.aws-sam/\|__pycache__/' .gitignore   # 既にあることを確認（重複追記を防ぐ）
printf '\r\n# SAM build artifacts\r\npackaged.yaml\r\nsamconfig.local.toml\r\n' >> .gitignore
tail -10 .gitignore
file .gitignore
```

`uv.lock` は**コミットする**（除外しない）。追記後に `sort .gitignore | uniq -d` が空であることを確認する。

- [ ] **Step 5: `Makefile` を作る**

`Makefile` はタブインデント必須。**CRLF の Makefile は GNU make で動くが、レシピ行の末尾 `\r` がコマンドに混ざる**ため、`Makefile` だけは LF で作る。`.gitattributes` の先頭行が `* -text` なので改行変換は行われず、作業ツリーの LF がそのまま保存される。**`.gitattributes` への追加指定は不要。**

`Makefile`（LF のまま。`sed` による CRLF 変換をしない）:

```makefile
.PHONY: test lint fmt validate build-shared build-core help

help:
	@echo "make test      - pytest (pyproject の testpaths 全体)"
	@echo "make lint      - ruff check + format --check"
	@echo "make fmt       - ruff format"
	@echo "make validate  - sam validate --lint (全テンプレート)"

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .

fmt:
	uv run ruff format .

validate:
	bash scripts/validate-templates.sh
```

- [ ] **Step 6: Makefile が LF であることを確認する**

```bash
cd /mnt/c/Users/devA/git/example-backoffice
file Makefile
# 期待: "ASCII text"（"with CRLF line terminators" が出たら LF に戻す: sed -i 's/\r$//' Makefile）
make help
```

- [ ] **Step 7: コミット**

```bash
cd /mnt/c/Users/devA/git/example-backoffice
git add .gitattributes .gitignore Makefile
printf 'mod refs #1001 monorepo 骨格と改行コード規約を追加\n' > /tmp/commit_message.txt
git commit -F /tmp/commit_message.txt
rm -f /tmp/commit_message.txt
git status --short
```

**受け入れ条件:** `.gitattributes` の先頭行が `* -text`（`* text=auto eol=crlf` ではない）。`git add .gitattributes` の時点で既存ファイル（`Documents/*.md` / `README.md`）が差分に現れない。`Makefile` が LF で `make help` が動く。`.gitignore` が CRLF のまま。`git status --short` が空。

---

### Task P0-2: Python / Node ツールチェーン

**Files:**
- Create: `/mnt/c/Users/devA/git/example-backoffice/pyproject.toml`
- Create: `/mnt/c/Users/devA/git/example-backoffice/frontend/package.json`
- Create: `/mnt/c/Users/devA/git/example-backoffice/frontend/tsconfig.json`
- Create: `/mnt/c/Users/devA/git/example-backoffice/frontend/tsconfig.node.json`
- Create: `/mnt/c/Users/devA/git/example-backoffice/frontend/vite.config.ts`
- Create: `/mnt/c/Users/devA/git/example-backoffice/frontend/vitest.config.ts`
- Create: `/mnt/c/Users/devA/git/example-backoffice/frontend/eslint.config.js`
- Create: `/mnt/c/Users/devA/git/example-backoffice/frontend/index.html`
- Create: `/mnt/c/Users/devA/git/example-backoffice/frontend/src/main.ts`（最小版。Task P4-2 で拡張）
- Create: `/mnt/c/Users/devA/git/example-backoffice/frontend/src/env.d.ts`
- Create: `/mnt/c/Users/devA/git/example-backoffice/frontend/src/smoke.test.ts`

**流用元:** `/mnt/c/Users/devA/git/example-portal/` のフロントエンド設定一式（`package.json` の scripts、`tsconfig` の構成、`eslint.config.js`）。ただし**版は設計 9.2 の表を優先**する（example-portal は 1 世代古い）。

**Interfaces:**
- Produces: `uv run pytest` / `uv run ruff` / `npm test` / `npm run build` が動く状態。Phase 1 以降の全タスクがこれらのコマンドを使う。

- [ ] **Step 1: `pyproject.toml` を作る**

```toml
[project]
name = "example-backoffice"
version = "0.1.0"
description = "社内保守管理サイト example-backoffice のバックエンド"
requires-python = ">=3.12"

[dependency-groups]
dev = [
    "pytest>=9.0",
    "pytest-cov>=6.0",
    "moto[dynamodb,s3,sqs,ssm,ses,cognitoidp,stepfunctions]>=5.1",
    "responses>=0.25",
    "jsonschema>=4.23",
    "boto3>=1.37.27",
    "aws-lambda-powertools>=3.7",
    "XlsxWriter>=3.2.9",
    "openpyxl>=3.1.5",
    "ruff>=0.9",
    "pycognito>=2024.5",
]

[tool.pytest.ini_options]
testpaths = ["shared/tests", "infra/core/tests", "apps"]
pythonpath = [
    "shared/layers",
    "infra/core/src",
]
addopts = "-ra --strict-markers --import-mode=importlib"
filterwarnings = ["error::DeprecationWarning:example_backoffice.*"]

[tool.coverage.run]
branch = true
omit = ["*/tests/*", "*/.venv/*"]

[tool.ruff]
line-length = 120
target-version = "py312"
exclude = [".venv", ".aws-sam", "node_modules", "frontend"]

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "S", "C4", "SIM"]
ignore = ["S101"]

[tool.ruff.lint.per-file-ignores]
"**/tests/**" = ["S105", "S106", "S311"]
```

**`--import-mode=importlib` は必須。** 本リポジトリには `shared/tests/` `infra/core/tests/` `tests/infra/` と**同名 basename のテストディレクトリ／モジュールが複数**ある。既定の `prepend` import mode だと `conftest.py` / `test_*.py` がトップレベルの同名モジュールとして衝突し、`import file mismatch` になる。回避のために `__init__.py` を置く方法もあるが、`importlib` モードなら `__init__.py` 無しで衝突しない。**したがって `shared/tests/__init__.py` / `infra/core/tests/__init__.py` / `tests/infra/__init__.py` は作らない**（テストディレクトリは名前空間パッケージにしない）。

`pycognito` は Task P3-4 の `scripts/get_id_token.py`（SRP で ID トークンを取得）が使う dev 依存（前提と判断 13）。

- [ ] **Step 2: 仮想環境を作り依存を入れる**

```bash
cd /mnt/c/Users/devA/git/example-backoffice
uv venv --python 3.12
uv sync --group dev
uv run python -c "import sys; print(sys.version)"
uv run pytest --version
uv run ruff --version
```

- [ ] **Step 3: `frontend/package.json` を作る**

版は設計 9.2 の表どおり。`npm install` が失敗したら**その版を報告して**最新安定版に落とす。

```json
{
  "name": "example-backoffice-frontend",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vue-tsc -b && vite build",
    "preview": "vite preview",
    "test": "vitest run",
    "test:watch": "vitest",
    "lint": "eslint . --max-warnings 0",
    "lint:fix": "eslint . --fix",
    "e2e": "playwright test"
  },
  "dependencies": {
    "@mdi/font": "^7.4",
    "aws-amplify": "^6.20",
    "axios": "^1.20",
    "pinia": "^4.0",
    "qrcode": "^1.5",
    "vue": "^3.5.40",
    "vue-router": "^5.2",
    "vuetify": "^4.1"
  },
  "devDependencies": {
    "@playwright/test": "^1.62",
    "@types/qrcode": "^1.5",
    "@vitejs/plugin-vue": "^6.0",
    "@vue/devtools-api": "^7.7",
    "@vue/test-utils": "^2.4",
    "eslint": "^9.20",
    "eslint-plugin-vue": "^10.0",
    "jsdom": "^29",
    "sass": "^1.83",
    "typescript": "~6.0",
    "typescript-eslint": "^8.24",
    "vite": "^8.2",
    "vite-plugin-vuetify": "^2.1",
    "vitest": "^4.1",
    "vue-tsc": "^3.3"
  }
}
```

- [ ] **Step 4: `frontend/tsconfig.json` と `frontend/tsconfig.node.json` を作る**

`tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noEmit": true,
    "jsx": "preserve",
    "isolatedModules": true,
    "verbatimModuleSyntax": true,
    "skipLibCheck": true,
    "resolveJsonModule": true,
    "types": ["vite/client", "vitest/globals"],
    "baseUrl": ".",
    "paths": { "@/*": ["./src/*"] }
  },
  "include": ["src/**/*.ts", "src/**/*.vue", "e2e/**/*.ts", "*.config.ts"],
  "references": [{ "path": "./tsconfig.node.json" }]
}
```

`tsconfig.node.json`:

```json
{
  "compilerOptions": {
    "composite": true,
    "module": "ESNext",
    "moduleResolution": "bundler",
    "skipLibCheck": true,
    "noEmit": true,
    "types": ["node"]
  },
  "include": ["vite.config.ts", "vitest.config.ts", "playwright.config.ts"]
}
```

- [ ] **Step 5: `frontend/vite.config.ts` と `frontend/vitest.config.ts` を作る**

`vite.config.ts`:

```ts
import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vuetify from 'vite-plugin-vuetify'

export default defineConfig({
  plugins: [
    vue(),
    vuetify({ autoImport: true, styles: { configFile: 'src/styles/settings.scss' } }),
  ],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  server: {
    proxy: {
      '/api': {
        target: process.env.VITE_API_PROXY_TARGET ?? 'https://backoffice-dev.example-cloud.com',
        changeOrigin: true,
      },
    },
  },
  build: { sourcemap: false, chunkSizeWarningLimit: 1200 },
})
```

`vitest.config.ts`:

```ts
import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    include: ['src/**/*.{test,spec}.ts'],
    server: { deps: { inline: ['vuetify'] } },
    coverage: { provider: 'v8', reporter: ['text', 'html'], include: ['src/**/*.{ts,vue}'] },
  },
})
```

- [ ] **Step 6: `frontend/eslint.config.js` を作る**

```js
import eslint from '@eslint/js'
import tseslint from 'typescript-eslint'
import pluginVue from 'eslint-plugin-vue'

export default tseslint.config(
  { ignores: ['dist/**', 'coverage/**', 'playwright-report/**', 'test-results/**'] },
  eslint.configs.recommended,
  ...tseslint.configs.recommended,
  ...pluginVue.configs['flat/recommended'],
  {
    files: ['**/*.vue'],
    languageOptions: { parserOptions: { parser: tseslint.parser } },
  },
  {
    rules: {
      'vue/multi-word-component-names': 'off',
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_' }],
    },
  },
)
```

`@eslint/js` を devDependencies に追加すること（Step 3 の JSON に `"@eslint/js": "^9.20"` を足す）。

- [ ] **Step 7: `frontend/index.html` を作る（`lang="ja"` 必須）**

```html
<!DOCTYPE html>
<html lang="ja">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>example-backoffice</title>
  </head>
  <body>
    <div id="app"></div>
    <script type="module" src="/src/main.ts"></script>
  </body>
</html>
```

- [ ] **Step 8: 最小の `src/main.ts` / `src/env.d.ts` / スモークテストを作る**

`src/main.ts`（Task P4-2 で本実装に置き換える）:

```ts
import { createApp } from 'vue'

const app = createApp({ template: '<div>example-backoffice</div>' })
app.mount('#app')
```

`src/env.d.ts`:

```ts
/// <reference types="vite/client" />

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<Record<string, never>, Record<string, never>, unknown>
  export default component
}

import 'vue-router'

declare module 'vue-router' {
  interface RouteMeta {
    requiresAuth?: boolean
    requiredGroups?: string[]
    appId?: string
    title?: string
    layout?: 'shell' | 'auth'
  }
}
```

`src/smoke.test.ts`（ツールチェーンが動くことだけを確認する。Task P4-1 で削除してよい）:

```ts
import { describe, expect, it } from 'vitest'

describe('toolchain', () => {
  it('runs vitest', () => {
    expect(1 + 1).toBe(2)
  })
})
```

- [ ] **Step 9: `src/styles/settings.scss` を作る（vite-plugin-vuetify が参照する）**

```scss
@use 'vuetify/settings' with (
  $body-font-family: ('Yu Gothic UI', 'Meiryo', 'Hiragino Kaku Gothic ProN', 'Noto Sans JP', 'Segoe UI', sans-serif),
  $heading-font-family: ('Yu Gothic UI', 'Meiryo', 'Hiragino Kaku Gothic ProN', 'Noto Sans JP', sans-serif)
);
```

**中華フォント（SimSun / SimHei / Microsoft YaHei / PingFang SC / Noto Sans SC）をスタックに入れてはいけない。**

- [ ] **Step 10: install → テスト → build を通す**

```bash
cd /mnt/c/Users/devA/git/example-backoffice/frontend
npm install
npm test
npm run lint
npm run build
```

`npm install` が版解決で失敗した場合は、失敗した版と落とした版を**タスク報告に明記する**（設計 9.2 は編集しない）。

- [ ] **Step 11: 改行コードを CRLF に変換して確認する**

`.sh` は無いので全ファイルが対象。**`Makefile` は対象外**（LF 固定）。

```bash
cd /mnt/c/Users/devA/git/example-backoffice
for f in pyproject.toml frontend/package.json frontend/tsconfig.json frontend/tsconfig.node.json \
         frontend/vite.config.ts frontend/vitest.config.ts frontend/eslint.config.js \
         frontend/index.html frontend/src/main.ts frontend/src/env.d.ts \
         frontend/src/smoke.test.ts frontend/src/styles/settings.scss; do
  file "$f" | grep -q CRLF || sed -i 's/$/\r/' "$f"
  file "$f"
done
```

`npm install` が生成した `package-lock.json` は npm が出力したまま（LF）でよい。`.gitattributes` の `* -text` により改行変換は行われないため、LF のまま保存される。

- [ ] **Step 12: 変換後にもう一度テストが通ることを確認する**

```bash
cd /mnt/c/Users/devA/git/example-backoffice
uv run ruff check .
cd frontend && npm test && npm run build
```

- [ ] **Step 13: コミット**

```bash
cd /mnt/c/Users/devA/git/example-backoffice
git add pyproject.toml uv.lock frontend/
printf 'mod refs #1001 Python(uv/ruff/pytest) と Node(vite/vitest/eslint) のツールチェーンを追加\n' > /tmp/commit_message.txt
git commit -F /tmp/commit_message.txt
rm -f /tmp/commit_message.txt
git status --short
```

---

### Task P0-3: cfn-lint 更新とテンプレート検証スクリプト

**Files:**
- Create: `/mnt/c/Users/devA/git/example-backoffice/scripts/validate-templates.sh`（**LF**）
- Modify: `/mnt/c/Users/devA/git/example-backoffice/pyproject.toml`（dev 依存に `cfn-lint>=1.51.3` を追加）

**Interfaces:**
- Produces: `bash scripts/validate-templates.sh` — 全テンプレートに `sam validate --lint` を掛ける。Phase 1 以降のテンプレートタスクが完了条件に使う。

- [ ] **Step 1: 現在の cfn-lint 版を確認する**

```bash
cfn-lint --version
# 実測: 1.43.4（1.51.3 未満なので更新が必要）
```

- [ ] **Step 2: `pyproject.toml` の dev グループに `cfn-lint>=1.51.3` を追加する**

```toml
dev = [
    ...
    "cfn-lint>=1.51.3",
]
```

- [ ] **Step 3: 同期して版を確認する**

```bash
cd /mnt/c/Users/devA/git/example-backoffice
uv sync --group dev
uv run cfn-lint --version
# 期待: 1.51.3 以上
```

`sam validate --lint` は **SAM CLI 自身が入っている venv の cfn-lint** を使う。この PC の `sam` は `/home/devA/.local/venvs/ref-project-b/bin/sam`（shebang が同 venv の python3）なので、`python3 -m pip install --user` では更新されない。**その venv の pip を直接叩く。**

```bash
# 現在 sam が使っている cfn-lint を確認
command -v sam
head -1 "$(command -v sam)"          # 期待: #!/home/devA/.local/venvs/ref-project-b/bin/python3
/home/devA/.local/venvs/ref-project-b/bin/python3 -m cfnlint --version

# 更新（--user は使わない）
/home/devA/.local/venvs/ref-project-b/bin/pip install -U 'cfn-lint>=1.51.3'
/home/devA/.local/venvs/ref-project-b/bin/python3 -m cfnlint --version
# 期待: 1.51.3 以上
```

`sam` が別パスにある場合は `head -1 "$(command -v sam)"` の shebang が指す python の `-m pip` を使う。

- [ ] **Step 3b: `sam validate --lint` が更新後の cfn-lint で動くことを確認する**

テンプレートがまだ無いので、既存プロジェクトのテンプレートで確認する（**他プロジェクトのファイルは読むだけ。変更しない**）。

```bash
cd /mnt/c/Users/devA/git/ref-project-a/stacks/auth
sam validate --lint
# 期待: エラーなく終了（終了コード 0）。cfn-lint の版不整合エラーが出ないこと
cd /mnt/c/Users/devA/git/example-backoffice
```

版が上がらない・`sam validate --lint` が落ちる場合は、**更新前の版と実行したコマンド・エラー全文を報告して止まる**（`--user` へのフォールバックはしない）。

- [ ] **Step 4: `scripts/validate-templates.sh` を作る（LF）**

```bash
#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATES=(
  "shared"
  "infra/core"
  "infra/edge"
)

for app_dir in "$ROOT"/apps/*/; do
  [ -f "$app_dir/template.yaml" ] && TEMPLATES+=("apps/$(basename "$app_dir")")
done

rc=0
for t in "${TEMPLATES[@]}"; do
  dir="$ROOT/$t"
  if [ ! -f "$dir/template.yaml" ]; then
    echo "skip (not yet created): $t"
    continue
  fi
  echo "=== sam validate --lint: $t ==="
  ( cd "$dir" && sam validate --lint ) || rc=1
done
exit "$rc"
```

- [ ] **Step 5: 実行権限を付けて動かす（テンプレートは未作成なので全部 skip になる）**

```bash
cd /mnt/c/Users/devA/git/example-backoffice
chmod +x scripts/validate-templates.sh
bash scripts/validate-templates.sh
# 期待: "skip (not yet created): shared" 等が並び、終了コード 0
echo "exit=$?"
```

- [ ] **Step 6: 改行コードを確認する（`.sh` は LF のまま）**

```bash
cd /mnt/c/Users/devA/git/example-backoffice
file scripts/validate-templates.sh
# 期待: "Bourne-Again shell script, ASCII text executable"（CRLF が付いていないこと）
file pyproject.toml
# 期待: CRLF（P0-2 で変換済み。編集で LF に戻っていたら sed -i 's/$/\r/' で戻す）
```

- [ ] **Step 7: コミット**

```bash
cd /mnt/c/Users/devA/git/example-backoffice
git add scripts/validate-templates.sh pyproject.toml uv.lock
printf 'mod refs #1001 cfn-lint を 1.51.3 以上へ更新しテンプレート検証スクリプトを追加\n' > /tmp/commit_message.txt
git commit -F /tmp/commit_message.txt
rm -f /tmp/commit_message.txt
git status --short
```

---

> **以降のタスクの読み方:** Phase 1 以降は「目的 / ファイル / 流用元 / 公開インターフェース / テストケース一覧 / 手順 / 受け入れ条件」で書く。**実装コードは実行者（Opus サブエージェント）が設計書を読んで書く。** 各タスクは TDD（テスト作成 → 失敗確認 → 実装 → 成功確認）で進め、末尾で CRLF 確認とコミットまで行う。共通の検証コマンド・CRLF 変換手順・コミット手順は Global Constraints を参照する。

### Task P0-4: テンプレート静的検査（タグ規約を pytest で強制する）

**目的:** 設計 14.2 のタグ規約はレビューだと漏れる。全テンプレートに対し `Project` / `Name` タグと `ProjectName` パラメータ、LogGroup の保持 365 日を pytest で機械検査する仕組みを先に作り、以降の全テンプレートタスクの受け入れ条件にする。

**Files:**
- Create: `tests/infra/conftest.py`, `tests/infra/test_template_tags.py`
- Modify: `pyproject.toml`（`testpaths` に `tests/infra` を追加、dev 依存に `pyyaml>=6.0`）

**`tests/infra/__init__.py` は作らない。** Task P0-2 で `addopts` に `--import-mode=importlib` を入れてあるため、`shared/tests/` `infra/core/tests/` `tests/infra/` の同名モジュール（`conftest.py` など）が衝突しない。`__init__.py` を置くとむしろ `tests` というトップレベルパッケージ名を占有して衝突源になる。

**流用元（そのまま持ち込む）:**
- `/mnt/c/Users/devA/git/ref-project-a/tests/infra/conftest.py` — CloudFormation 短縮タグ（`!Ref` `!Sub` `!GetAtt` `!Join` 等 16 種）を `yaml.SafeLoader` の `add_constructor` で辞書化する `CfnLoader`。
- `/mnt/c/Users/devA/git/ref-project-a/tests/infra/test_template_resources.py` — `_get_resources_by_type()` / `_assert_tags()`。

**公開インターフェース:**
- fixture `load_template(relative_path: str) -> dict | None` — リポジトリルート相対。未作成なら `None`（呼び出し側で `pytest.skip`）。
- `CfnLoader(yaml.SafeLoader)` — `!GetStackOutput` も含めて登録する。
- モジュール定数 `TEMPLATES: list[str]` = `["shared/template.yaml", "infra/core/template.yaml", "infra/edge/template.yaml"]`。app スタックは `apps/*/template.yaml` を glob で追加する。
- モジュール定数 `ALLOWED_NAME_VALUES`。設計 14.2 の値（`Function` `Api` `Bucket` `Distribution` `Queue` `Table` `LogGroup` `Role` `UserPool` `StateMachine` `Schedule`）＋ 本計画で実際に使う値（`Topic` = `AWS::SNS::Topic`、`Alarm` = `AWS::CloudWatch::Alarm`、`ConfigurationSet` = `AWS::SES::ConfigurationSet`、`EdgeFunction` = `AWS::CloudFront::Function`。前提と判断 16）。**WAF 廃止により `WebACL` は使わない**（前提と判断 18）。
  - **許容値外のタグ対応型が出たときの規則:** 既存の値へ無理に寄せず、(1) `ALLOWED_NAME_VALUES` に新しい役割名を追加、(2) タスク報告に追加した値と対象リソース型を明記、(3) **設計 14.2 の表への追記をユーザーに提案する**（設計書は編集しない）。この 3 点をテストファイルの先頭コメントにも書いておく。
- モジュール定数 `UNTAGGABLE_TYPES` — **タグ非対応型（テストの検査対象から外す集合）**。ここに入れた型はタグ有無を一切検査しない。**タグ対応かどうか迷ったら `sam validate --lint` / `cfn-lint` の結果と AWS ドキュメントで確認し、非対応と分かった型だけをここに追加する**（テストを黙らせるために対応型を入れてはいけない。追加したら理由を 1 行コメントで残す）。初期値: `AWS::CloudFront::OriginAccessControl` / `AWS::CloudFront::Function` / `AWS::Route53::RecordSet` / `AWS::Route53::RecordSetGroup` / `AWS::S3::BucketPolicy` / `AWS::Lambda::Permission` / `AWS::Cognito::UserPoolClient` / `AWS::Cognito::UserPoolGroup` / `AWS::Lambda::EventSourceMapping` / `AWS::SES::EmailIdentity` / `AWS::SES::ConfigurationSetEventDestination` / `AWS::IAM::Policy` / `AWS::Serverless::LayerVersion`（**`Tags` プロパティ自体が無い**）。**`AWS::SSM::Parameter` はタグ対応だが `Tags` が任意**であり、本計画では契約パラメータにタグを付けない方針のため `UNTAGGABLE_TYPES` に入れて検査対象から外す（理由をコメントに残す）。
- ヘルパ `_normalize_tags(resource) -> dict | None` — リスト形式 `[{Key, Value}]` とマップ形式 `{Key: Value}` の両方を dict に正規化。`AWS::Cognito::UserPool` は `UserPoolTags` を見る。
- ヘルパ `_effective_tags(template, logical_id, resource) -> dict | None` — **`Globals` の継承を考慮する**。`AWS::Serverless::Function` にリソース個別の `Tags` が無ければ `Globals.Function.Tags` を、`AWS::Serverless::HttpApi` は `Globals.HttpApi.Tags` を見る（SAM は Globals のタグを各リソースへ継承するため、テンプレートの生テキストだけを見ると「タグ無し」に誤判定する）。タグ規約テストは `_normalize_tags` ではなく**こちらを使う**。
- ヘルパ `_load_json_template(path) -> dict` — 任意の絶対パスの YAML を `CfnLoader` で読む（`load_template` はリポジトリルート相対専用なので、`/tmp` に展開した cookiecutter 雛形を読む Task P4-6 のテストはこちらを使う）。

**テストケース（`test_template_tags.py`。`TEMPLATES` で parametrize）:**
- `test_all_taggable_resources_have_project_and_name_tags` — タグ対応リソース全部に `Project` と `Name` がある。`Name` の値は `ALLOWED_NAME_VALUES` に含まれる。欠けているものを全部集めてから 1 回で assert する（1 個ずつ落ちない）。
- `test_project_name_parameter_exists` — `Parameters.ProjectName.Default == "example-backoffice"`。
- `test_log_groups_retain_365_days` — `AWS::Logs::LogGroup` の `RetentionInDays == 365`（設計 12.4）。

**手順:**
- [ ] 流用元 2 ファイルを読む（`cat`）。
- [ ] 上記テストを書く。テンプレートが 1 つも無いので**全件 skip になること**を `uv run pytest tests/infra -v` で確認する（エラーにならないこと）。
- [ ] `pyproject.toml` を更新し `uv sync --group dev`。
- [ ] `CfnLoader` が実在テンプレートを読めることを `/mnt/c/Users/devA/git/ref-project-a/stacks/auth/template.yaml` で確認する（`Resources` のキー一覧が出る）。
- [ ] CRLF 変換 → `file` 確認 → `uv run ruff check tests/` → コミット（`mod refs #1001 テンプレートのタグ規約を pytest で検査する仕組みを追加`）。

**受け入れ条件:** `uv run pytest tests/infra -v` が全件 skip で終了コード 0。`ruff check` が通る。全ファイルが CRLF。

---

## Phase 1: shared レイヤ

> **実装順の注意（前提と判断 1）:** shared を core より先に作る。core の notifier が `shared/schemas/notify.v1.json` を正本として参照するため。**デプロイ順は設計 3.3 のとおり core → shared のまま。**

### Task P1-1: 通知メッセージスキーマ `notify.v1.json`

**目的:** 設計 8.2 のメッセージスキーマ `example-backoffice.notify.v1` を JSON Schema（Draft 2020-12）として確定する。送信側（`notify.py`）と受信側（notifier）の**両方のテストがこの 1 ファイルを参照する**契約にする。

**Files:**
- Create: `shared/schemas/notify.v1.json`, `shared/tests/conftest.py`, `shared/tests/test_notify_schema.py`（**`shared/tests/__init__.py` は作らない**。Task P0-2 の `--import-mode=importlib` で同名モジュールの衝突を回避しているため）

**Spec:** 設計 8.2（フィールド表）。実例は `Documents/apps/saas-a-billing/DESIGN.md` 6.8 の JSON。

**公開インターフェース:**
- `shared/schemas/notify.v1.json` — `$schema` は `https://json-schema.org/draft/2020-12/schema`、`additionalProperties: false`、`required: ["schema","message_id","app","channels"]`。
  - `schema`: `const` = `"example-backoffice.notify.v1"`
  - `app`: kebab-case のパターン制約（3〜30 文字）
  - `channels`: `minItems: 1` / `uniqueItems: true` / `items.enum: ["email","slack"]`
  - `email`: `required: ["to","subject","text"]`、`cc` / `reply_to` / `html` は任意
  - `attachments[]`: `required: ["bucket","key","filename"]`、`content_type` 任意
  - `slack`: `required: ["text"]`、`channel` / `mention` / `blocks` 任意
  - `meta`: 任意の object
  - `allOf` の `if/then` で「`channels` に `email` を含むなら `email` 必須」「`slack` を含むなら `slack` 必須」を表す
- fixture `notify_schema`（session スコープ、`shared/tests/conftest.py`）— スキーマを dict で返す。
- fixture `valid_message` — 設計 6.8 の実例に沿った完全なメッセージ。

**テストケース（`test_notify_schema.py`。`jsonschema.Draft202012Validator` を使う）:**
- `test_schema_itself_is_valid` — `check_schema` が通る。
- `test_valid_message_passes` — `valid_message` が適合する。
- `test_schema_field_must_be_exact_constant` — `schema` を `...v2` にすると `ValidationError`。
- `test_required_top_level_fields`（parametrize: `schema` / `message_id` / `app` / `channels`）— 欠けると失敗。
- `test_channels_must_be_email_or_slack` — `["teams"]` は失敗。
- `test_channels_must_not_be_empty` — `[]` は失敗。
- `test_email_channel_requires_email_object` — `channels=["email"]` で `email` 無しは失敗。
- `test_slack_channel_requires_slack_text` — `slack.text` 欠落は失敗。
- `test_email_only_message_without_slack_passes` — `channels=["email"]` で `slack` 無しは成功。
- `test_attachment_requires_bucket_key_filename` — `filename` 欠落は失敗。
- `test_unknown_top_level_property_is_rejected` — 未知のトップレベルキーは失敗。

**手順:** テスト作成 → `uv run pytest shared/tests/test_notify_schema.py -v` で `FileNotFoundError` を確認 → スキーマ実装 → 全 passed 確認 → CRLF 変換・`file` 確認 → `ruff check shared/` → コミット（`mod refs #1001 通知メッセージスキーマ notify.v1.json を追加`）。

**受け入れ条件:** 上記テスト全 passed。CRLF。

---

### Task P1-2: shared `auth.py` / `response.py`

**目的:** JWT クレームの取り出しとグループ判定（設計 6.3 / 10.2）、API レスポンスの共通エンベロープ（設計 10.2）を実装する。

**Files:**
- Create: `shared/layers/example_backoffice/__init__.py`, `auth.py`, `response.py`
- Create: `shared/tests/test_auth.py`, `shared/tests/test_response.py`

**流用元:** `/mnt/c/Users/devA/git/ref-project-a/functions/api/helpers/auth.py` — クレームの取り出し方の考え方のみ。**ref-project-a は REST API のため `requestContext.authorizer.claims`。本件は HTTP API なので `requestContext.authorizer.jwt.claims` に読み替える。**

**公開インターフェース（`auth.py`）:**
- `class UnauthorizedError(Exception)` / `class ForbiddenError(Exception)`
- `@dataclass(frozen=True) class Claims: sub: str; email: str; groups: list[str]; raw: dict`
- `def normalize_groups(value) -> list[str]` — `"[admin dev]"` / `"[admin]"` / `"[]"` / `"admin"` / `"admin,dev"` / `["admin","dev"]` / `None` / `""` をすべて配列に正規化（設計 17 の #6 が未実測のため両形式を受理する）
- `def get_claims(event: dict) -> Claims` — クレーム不在または `sub` 不在で `UnauthorizedError`
- `def require_group(claims: Claims, group: str) -> None` — 不足で `ForbiddenError`
- `def is_admin(claims: Claims) -> bool`
- 定数 `GROUPS_CLAIM = "cognito:groups"`

**公開インターフェース（`response.py`）:**
- `def ok(data, meta: dict | None = None) -> dict` → `{"ok": True, "data": data, "error": None, "meta": meta}`
- `def error(code: str, message: str, status: int = 400) -> Response` — **設計 10.2 のシグネチャに揃える**。エンベロープ `{"ok": False, "data": None, "error": {"code", "message"}, "meta": None}` を組み、`json_response(payload, status)` の戻り値（Powertools `Response`）を返す。既定は 400。
- `def error_body(code: str, message: str) -> dict` — 上のエンベロープ dict だけを返す内部ヘルパ（テストが形を検査するために公開する）。
- `def json_response(payload: dict, status: int) -> Response` — Powertools の `Response`。`content_type="application/json"`、`json.dumps(..., ensure_ascii=False, default=str)`

**テストケース（`test_auth.py`）:** ヘルパ `make_event(claims: dict | None)` で HTTP API イベントを組む。
- `test_get_claims_reads_sub_and_email` — `sub` / `email` が取れ、`groups` は `[]`。
- `test_normalize_groups_accepts_both_formats`（parametrize 8 ケース）— 上記の全入力に対する期待配列。
- `test_get_claims_normalizes_group_string` / `..._group_list` — どちらも `["admin"]`。
- `test_get_claims_without_authorizer_raises_unauthorized`
- `test_get_claims_without_sub_raises_unauthorized`
- `test_require_group_passes_for_member` / `test_require_group_raises_forbidden_for_non_member`
- `test_is_admin` — admin あり `True` / なし `False`

**テストケース（`test_response.py`）:**
- `test_ok_wraps_data` / `test_ok_with_meta`（`meta` に `total/page/limit`）/ `test_error_body_shape`
- `test_error_returns_response_with_default_status_400` — `error("BAD_REQUEST", "...")` の `status_code == 400`。
- `test_error_accepts_explicit_status` — `error("NOT_FOUND", "...", 404)` の `status_code == 404`、body の `error.code` が一致。
- `test_json_response_sets_status_and_content_type` — 404 と `application/json`
- `test_json_response_serializes_japanese_without_escape` — 本文に日本語がそのまま入る

**手順:** テスト作成 → `uv run pytest shared/tests/test_auth.py shared/tests/test_response.py -v` で `ModuleNotFoundError` 確認 → 実装 → 全 passed → CRLF 変換・確認 → `ruff check shared/` → コミット（`mod refs #1001 shared レイヤに auth と response を追加`）。

**受け入れ条件:** 上記テスト全 passed。`ruff check` が通る。CRLF。

---

### Task P1-3: shared `origin_verify.py` / `logging.py`

**目的:** CloudFront 経由であることを `x-origin-verify` で確認するミドルウェア（設計 5.5）と、社内レイヤ `LambdaLogger` のラッパ（設計 10.2）。

**Files:**
- Create: `shared/layers/example_backoffice/origin_verify.py`, `logging.py`
- Create: `shared/tests/test_origin_verify.py`, `shared/tests/test_logging.py`

**流用元:** `/mnt/c/Users/devA/git/ref-project-a/functions/notifier/app.py` の 14-32 行 — **`example_powertools` の import フォールバック**（`try: from example_powertools.logging import LambdaLogger` / `except ImportError:` で標準 logging のダミーを定義し、`contextualize` を no-op デコレータにする）。ローカル pytest でレイヤ無しでも動かすために必須。

**公開インターフェース（`logging.py`）:**
- `LambdaLogger` — 社内レイヤの実体、または上記フォールバック。フォールバックは `debug/info/warning/error/exception(msg, details=None)` と `@staticmethod contextualize(func)` を持つ。ログレベルは環境変数 `LOG_LEVEL`（既定 `INFO`）。
- `def get_logger() -> LambdaLogger`

**公開インターフェース（`origin_verify.py`）:**
- 定数 `HEADER = "x-origin-verify"`、`CACHE_SECONDS = 300`
- `def secret_parameter_name() -> str` → `/example-backoffice/{ENV}/edge/origin-verify-secret`（環境変数 `ENV`、既定 `dev`）
- `def _get_secret() -> str` — Powertools `get_parameter(name, decrypt=True, max_age=300)`。テストが `monkeypatch.setattr` で差し替えられるようモジュール関数にする。
- `@lambda_handler_decorator def origin_verify(handler, event, context)` — ヘッダを**大文字小文字を区別せず**探し、`hmac.compare_digest` で定数時間比較。不一致・不在・取得失敗はいずれも 403 の API Gateway 形式 dict（`{"statusCode":403,"headers":{"content-type":"application/json"},"body":...}`、body の `error.code` は `ORIGIN_FORBIDDEN`）。**例外は投げない。**
- スキップ条件: 環境変数 `ORIGIN_VERIFY_SKIP == "1"` または `AWS_SAM_LOCAL == "true"`

**テストケース（`test_origin_verify.py`。autouse fixture で `ENV=dev` と `_get_secret` の差し替え）:**
- `test_matching_header_passes_through` — 200 が返る。
- `test_mismatched_header_returns_403` — `error.code == "ORIGIN_FORBIDDEN"`。
- `test_missing_header_returns_403`
- `test_header_lookup_is_case_insensitive` — `X-Origin-Verify` でも 200。
- `test_skip_flag_bypasses_verification` — `ORIGIN_VERIFY_SKIP=1` でヘッダ無しでも 200。
- `test_sam_local_bypasses_verification` — `AWS_SAM_LOCAL=true` で同上。
- `test_secret_lookup_failure_returns_403` — `_get_secret` が例外を投げても 403（500 にしない）。
- `test_parameter_name_uses_env` — `ENV=prod` で `/example-backoffice/prod/edge/origin-verify-secret`。

**テストケース（`test_logging.py`）:**
- `test_get_logger_returns_something_with_info` — `logger.info("test", {"key": "value"})` が例外なく動く（第 2 引数は dict をそのまま渡す。`extra=` ではない）。
- `test_contextualize_decorator_is_usable` — `@LambdaLogger.contextualize` を付けた関数が呼べる。

**手順:** 流用元を読む → テスト作成 → 失敗確認 → 実装 → 全 passed → CRLF 変換・確認 → `ruff check shared/` → コミット（`mod refs #1001 shared レイヤに origin_verify と logging を追加`）。

**受け入れ条件:** 上記 10 件が passed。CRLF。

---

### Task P1-4: shared `notify.py`

**目的:** アプリが呼ぶ唯一の通知 API（設計 8.3）。SQS にメッセージを投げ、添付を S3 に置く。

**Files:**
- Create: `shared/layers/example_backoffice/notify.py`, `shared/tests/test_notify.py`

**Spec:** 設計 8.2 / 8.3 / 11.2（S3 キー規約 `{app}/[{subprefix}/]{yyyy}/{mm}/{filename}`）。

**前提と判断 2 に従い、実行時検証は自前の軽量バリデータで行う（`jsonschema` に依存しない）。** テストでは `jsonschema` で「生成したメッセージが `shared/schemas/notify.v1.json` にも適合すること」を確認する。

**公開インターフェース:**
- 定数 `SCHEMA = "example-backoffice.notify.v1"`、`VALID_CHANNELS = ("email","slack")`、`JST = timezone(timedelta(hours=9))`
- `class NotifyValidationError(ValueError)`
- `@dataclass class Email: to: list[str]; subject: str; text: str; cc: list[str]|None=None; reply_to: list[str]|None=None; html: str|None=None`
- `@dataclass class Attachment: key: str; filename: str; bucket: str|None=None; content_type: str|None=None`
- `@dataclass class Slack: text: str; channel: str|None=None; mention: str|None=None; blocks: list[dict]|None=None`
- `def build_message(*, app, channels, email=None, attachments=None, slack=None, meta=None, message_id=None) -> dict` — 検証つきでメッセージ本体を組む（テストから直接呼べるよう公開）
- `def send(*, app, channels, email=None, attachments=None, slack=None, meta=None) -> str` — `message_id`（UUID4 文字列）を返す
- `def attachment_key(app, filename, subprefix, when: datetime) -> str`
- `def put_attachment(app, filename, data: bytes, subprefix=None, content_type=None, when=None) -> Attachment`
- `def attachments_bucket() -> str` — 環境変数 `ATTACHMENTS_BUCKET`
- `def reset_clients() -> None` — boto3 クライアントのモジュールキャッシュを捨てる（moto のためにテストが呼ぶ）
- 環境変数: `NOTIFY_QUEUE_URL`（必須。無ければ `NotifyValidationError`）、`ATTACHMENTS_BUCKET`

**テストケース（`test_notify.py`。`moto` の `mock_aws` で SQS キューと S3 バケットを作る fixture `aws`）:**
- `test_send_puts_message_on_queue` — 受信した body の `message_id` が戻り値と一致し、`schema` / `app` / `email.subject` が正しい。**`jsonschema` でスキーマ適合も確認する。**
- `test_send_includes_slack_and_attachments` — `attachments[0].bucket` が `ATTACHMENTS_BUCKET` で補完される。`slack.channel` / `meta.run_id` が入る。スキーマ適合。
- `test_send_generates_unique_message_ids` — 2 回呼ぶと別 ID。
- `test_send_rejects_email_channel_without_email`
- `test_send_rejects_slack_channel_without_slack`
- `test_send_rejects_unknown_channel` — `["teams"]`。
- `test_send_rejects_empty_channels`
- `test_send_rejects_invalid_app_id` — `"Bad_App"`。
- `test_put_attachment_uses_key_convention` — `when=2026-08-21(JST)` → `saas-a-billing/2026/08/saas_a_billing_2026-08.xlsx`。S3 から読み戻して内容一致。
- `test_put_attachment_with_subprefix` — `subprefix="production"` → `saas-a-billing/production/2026/10/x.xlsx`。
- `test_put_attachment_sets_content_type` — `head_object` の `ContentType` が一致。
- `test_build_message_matches_schema_for_every_channel_combination` — `["email"]` / `["slack"]` / `["email","slack"]` の 3 通りがスキーマ適合。

**手順:** テスト作成 → 失敗確認 → 実装 → 全 passed → `uv run pytest shared/tests --cov=example_backoffice --cov-report=term-missing --cov-fail-under=80` → CRLF 変換・確認 → `ruff check shared/` → コミット（`mod refs #1001 shared レイヤに notify クライアントを追加`）。

**受け入れ条件:** 上記 12 件が passed。`example_backoffice` パッケージのカバレッジ 80% 以上。CRLF。

---

### Task P1-5: shared スタックのテンプレートと samconfig

**目的:** shared レイヤを公開し、`/shared/layer-arn` と `/shared/slack/mode` を SSM に書く（設計 3.1 / 4.2 / 10.1 / 13.2）。

**Files:**
- Create: `shared/template.yaml`, `shared/samconfig.toml`, `shared/layers/requirements.txt`（空）
- Create: `tests/infra/test_shared_template.py`
- Modify: `tests/infra/test_template_tags.py`（必要なら `UNTAGGABLE_TYPES` を調整。`AWS::Serverless::LayerVersion` は Task P0-4 の初期値に既に入っている）

**流用元:** `/mnt/c/Users/devA/git/ref-project-a/samconfig.toml` — 2 環境構成、`s3_bucket = "example-sam-ap-northeast-1"`、`capabilities`。**本件の 2 つ目の環境名は `[prod]`**（ref-project-a の `[production]` ではない。設計 13.2）。

**テンプレートのリソース:**
| 論理 ID | 型 | 要点 |
| --- | --- | --- |
| `SharedLayer` | `AWS::Serverless::LayerVersion` | `LayerName: ${ProjectName}-shared-${Env}`、`ContentUri: layers/`、`CompatibleRuntimes: [python3.13]`、`CompatibleArchitectures: [x86_64]`、`RetentionPolicy: Retain`、`DeletionPolicy: Retain`、`Metadata.BuildMethod: python3.13` |
| `SharedLayerArnParameter` | `AWS::SSM::Parameter` | `Name: /${ProjectName}/${Env}/shared/layer-arn`、`Type: String`、`Value: !Ref SharedLayer` |
| `SlackModeParameter` | `AWS::SSM::Parameter` | `Name: /${ProjectName}/${Env}/shared/slack/mode`、`Value: !Ref SlackMode` |

**Parameters:** `ProjectName`（既定 `example-backoffice`）、`Env`（`AllowedValues: [dev, prod]`、既定 `dev`）、`SlackMode`（`AllowedValues: [bot, webhook]`、既定 `webhook`）。
**Outputs:** `SharedLayerArn`、`SlackModeParameterName`。

**samconfig（`shared/samconfig.toml`。設計 13.2 / 14.1）:** セクション名まで含めて次の形にする。**`use_container` は `deploy` ではなく `build` セクション**に置く。2 つ目の環境名は `[prod]`（ref-project-a の `[production]` ではない。設計 13.2）。

```toml
version = 0.1

[default.build.parameters]
use_container = true

[default.deploy.parameters]
stack_name = "example-backoffice-shared-dev"
region = "ap-northeast-1"
s3_bucket = "example-sam-ap-northeast-1"
s3_prefix = "example-backoffice-shared-dev"
capabilities = "CAPABILITY_IAM"
confirm_changeset = false
tags = "Project=example-backoffice App=shared Env=dev"
parameter_overrides = "ProjectName=example-backoffice Env=dev SlackMode=webhook"

[production.build.parameters]
use_container = true

[production.deploy.parameters]
stack_name = "example-backoffice-shared-prod"
region = "ap-northeast-1"
s3_bucket = "example-sam-ap-northeast-1"
s3_prefix = "example-backoffice-shared-prod"
capabilities = "CAPABILITY_IAM"
confirm_changeset = true
tags = "Project=example-backoffice App=shared Env=prod"
parameter_overrides = "ProjectName=example-backoffice Env=prod SlackMode=webhook"
```

`AWS_PROFILE` は samconfig に書かない（環境変数）。CP-1 で Slack Bot token を登録したら **`SlackMode=bot` に直してコミットする**（前提と判断 / CP-1 の注記）。

**注意:** `sam build` は `ContentUri` 直下の `requirements.txt` を見る。`shared/layers/requirements.txt` を空で作る（`shared/layers/example_backoffice/requirements.txt` ではない）。

**テストケース（`tests/infra/test_shared_template.py`。`load_template` が `None` なら skip）:**
- `test_defines_a_layer_version` — LayerVersion がちょうど 1 つ、`CompatibleRuntimes == ["python3.13"]`、`ContentUri == "layers/"`、`DeletionPolicy == "Retain"`。
- `test_writes_layer_arn_parameter` — `/shared/layer-arn` を書く `AWS::SSM::Parameter` が 1 つ、`Type == "String"`。
- `test_writes_slack_mode_parameter` — `/shared/slack/mode` を書く SSM パラメータが 1 つ。
- `test_slack_mode_parameter_is_constrained` — `Parameters.SlackMode.AllowedValues == ["bot","webhook"]`、`Default == "webhook"`。
- `test_env_parameter_is_constrained` — `Parameters.Env.AllowedValues == ["dev","prod"]`。

**手順:**
- [ ] テスト作成 → `uv run pytest tests/infra/test_shared_template.py -v` で 5 skipped を確認。
- [ ] `shared/template.yaml` / `samconfig.toml` / `layers/requirements.txt` を作る。
- [ ] `cd shared && sam validate --lint` が通る。
- [ ] `uv run pytest tests/infra -v` — 新規 5 件とタグ規約テストが passed。
- [ ] `cd shared && sam build --use-container` が通り、`.aws-sam/build/SharedLayer/python/example_backoffice/` に 6 ファイル（`__init__` `auth` `logging` `notify` `origin_verify` `response`）が入る。**`public.ecr.aws/sam/build-python3.13` が取得できない場合は報告する（要確認 #5）。デプロイはしない。**
- [ ] CRLF 変換・確認 → コミット（`mod refs #1001 shared スタックのテンプレートと samconfig を追加`）。

**受け入れ条件:** `sam validate --lint` が通る。`tests/infra` 全 passed（**タグ規約テストを含む**）。`sam build --use-container` 成功。samconfig に `[default.build.parameters]` / `[default.deploy.parameters]` / `[production.*]` の 4 セクションがあり、`stack_name` = `example-backoffice-shared-{env}`、`region = "ap-northeast-1"`、`tags` に `App=shared` と `Env` がある。CRLF。

---

## Phase 2: core スタック

### Task P2-1: PreSignUp Lambda（`@example.co.jp` 限定）

**目的:** セルフサインアップを社内ドメインに限定する（設計 6.2）。

**Files:**
- Create: `infra/core/src/pre_signup/app.py`
- Create: `infra/core/tests/conftest.py`, `infra/core/tests/test_pre_signup.py`（**`infra/core/tests/__init__.py` は作らない**。Task P0-2 の `--import-mode=importlib` による）

**流用元（コードとテストの両方をほぼそのまま持ち込む）:**
- `/mnt/c/Users/devA/git/ref-project-a/functions/cognito_trigger/app.py`（15 行、外部依存ゼロ）
- `/mnt/c/Users/devA/git/ref-project-a/tests/unit/test_cognito_trigger.py`（5 ケース）

**公開インターフェース:**
- 定数 `ALLOWED_DOMAIN = "example.co.jp"`
- `def handler(event: dict, context) -> dict` — `event["request"]["userAttributes"]["email"]` のドメインが `example.co.jp` でなければ `Exception` を投げる（Cognito は例外で登録を拒否する）。通れば `event` をそのまま返す。
- `requirements.txt` は作らない（依存ゼロ）。テンプレート側でも `Layers` を書かない（**`Globals.Function` に `Layers` は置かない**ので打ち消しは不要。レイヤが要る関数だけが個別に `Layers` を書く）。

**テストケース（`test_pre_signup.py`）:**
- `test_allows_example_domain` — `devA@example.co.jp` は `event` をそのまま返す。
- `test_allows_uppercase_domain` — `DEVA@EXAMPLE.CO.JP` も許可（小文字化して比較する）。
- `test_rejects_external_domain` — `someone@gmail.com` は `Exception`。
- `test_rejects_lookalike_domain` — `a@fake-example.co.jp` は `Exception`（`endswith` ではなく完全一致で比較する）。
- `test_rejects_missing_email` — `userAttributes` に `email` が無ければ `Exception`。
- `test_rejects_email_without_at` — `"noatmark"` は `Exception`。

**`infra/core/tests/conftest.py`:** ダミー AWS 認証情報（`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_DEFAULT_REGION=ap-northeast-1`）を `autouse` の `monkeypatch` で設定する（流用元: `/mnt/c/Users/devA/git/ref-project-a/tests/integration/api/conftest.py`）。

**手順:** 流用元 2 ファイルを読む → テスト作成 → 失敗確認 → 実装 → 全 passed → CRLF 変換・確認 → `ruff check infra/` → コミット（`mod refs #1001 core に PreSignUp Lambda を追加`）。

**受け入れ条件:** 6 件 passed。`pythonpath` に `infra/core/src` があるため `from pre_signup.app import handler` で import できる。CRLF。

---

### Task P2-2: notifier の SES 送信部と Slack 投稿部

**目的:** SES v2 `SendEmail`（Simple + Attachments）と Slack 投稿（Bot token / Webhook）を、SQS ハンドラから切り離した純粋な送信モジュールとして実装する（設計 8.4 / 8.5）。

**Files:**
- Create: `infra/core/src/notifier/__init__.py`（空）, `ses_sender.py`, `slack_sender.py`
- Create: `infra/core/src/notifier/requirements.txt` — `boto3>=1.37.27` の 1 行（SES v2 `Attachments` 対応の最小版。設計 8.1 / 8.4）
- Create: `infra/core/tests/test_ses_sender.py`, `infra/core/tests/test_slack_sender.py`

**流用元:**
- `/mnt/c/Users/devA/git/ref-project-a/functions/notifier/email_sender.py` — クライアントの遅延生成、`Charset: "UTF-8"`、宛先の正規化、JST 変換ヘルパ。**ただし本件は SES v1 `send_email` ではなく v2 `sesv2.send_email` で `Content.Simple` + `Attachments[]` を使う。**
- `/mnt/c/Users/devA/git/ref-project-a/functions/notifier/slack_sender.py` — SSM から Webhook URL を取得して `urllib` で POST する流れ、`@here` 等のメンション変換。**本件は `requests` を使わず `urllib.request` で実装する**（設計 8.5「依存: `urllib`」）。

**公開インターフェース（`ses_sender.py`）:**
- `def send_email(*, message: dict, from_address: str, s3_client=None, sesv2_client=None) -> str` — `message["email"]` と `message.get("attachments")` から SES v2 `send_email` を組み立てて送る。戻り値は `MessageId`。
  - `FromEmailAddress` は `"example-backoffice <{from_address}>"` 形式
  - `Destination.ToAddresses` / `CcAddresses`、`ReplyToAddresses`
  - `Content.Simple.Subject.Data`（`Charset="UTF-8"`）、`Body.Text.Data`、`html` があれば `Body.Html.Data`
  - `Content.Simple.Attachments[]` = `{"RawContent": <S3 から取得した bytes>, "FileName": ..., "ContentType": ...}`（`content_type` 未指定なら `application/octet-stream`）
- `class AttachmentNotFoundError(Exception)` — S3 に添付が無い場合（**恒久エラー。設計 8.6**）
- `def fetch_attachment(bucket: str, key: str, s3_client=None) -> bytes` — `NoSuchKey` / `404` を `AttachmentNotFoundError` に変換
- 環境変数: `SES_FROM_ADDRESS`

**公開インターフェース（`slack_sender.py`）:**
- `def send_slack(*, message: dict, ssm_prefix: str, http_post=None) -> None`
- `def resolve_mode(ssm_prefix: str) -> tuple[str, str]` — `("bot", token)` または `("webhook", url)`。**bot-token があれば bot、無ければ webhook-url、両方無ければ `SlackNotConfiguredError`（前提と判断 7 / 要確認 #1）。** Powertools `get_parameter(..., decrypt=True, max_age=300)` を使い、`ParameterNotFound` を握って次の候補へ進む。
- `class SlackNotConfiguredError(Exception)`（恒久エラー扱い）
- `def build_text(message: dict) -> str` — `slack.mention` があれば本文先頭に付け、改行で区切る。空なら本文のみ。
- bot: `POST https://slack.com/api/chat.postMessage`、`Authorization: Bearer <token>`、`Content-Type: application/json; charset=utf-8`、body に `channel` / `text`（`blocks` があれば併せて）。**応答の `ok` が false なら例外**（`error` の値をメッセージに含める。token 本体はログにもメッセージにも出さない）。
- webhook: `POST <url>`、body は `{"text": ...}`。`slack.channel` は無視する。
- SSM キー: `{ssm_prefix}/shared/slack/bot-token`、`{ssm_prefix}/shared/slack/webhook-url`。`ssm_prefix` は `/example-backoffice/{ENV}`。

**テストケース（`test_ses_sender.py`。`moto` の `mock_aws` で S3 と SES v2）:**
- `test_sends_simple_email` — 件名・本文・宛先が渡る。
- `test_includes_cc_and_reply_to`
- `test_includes_html_body_when_present`
- `test_attaches_object_from_s3` — S3 に置いた bytes が `Attachments[0].RawContent` に入り、`FileName` が `message` の値と一致。
- `test_attachment_content_type_defaults_to_octet_stream`
- `test_missing_attachment_raises_attachment_not_found` — キーが無ければ `AttachmentNotFoundError`。
- `test_from_address_uses_display_name` — `example-backoffice <noreply@...>`。
- `test_japanese_subject_is_utf8` — 日本語件名がそのまま渡る。

**テストケース（`test_slack_sender.py`。`http_post` をスタブに差し替え、SSM は `moto` で作る）:**
- `test_uses_bot_token_when_present` — `chat.postMessage` に `Authorization: Bearer` と `channel` が渡る。
- `test_falls_back_to_webhook_when_bot_token_absent` — Webhook URL に `{"text": ...}` が POST され、`channel` は送られない。
- `test_raises_when_neither_configured` — `SlackNotConfiguredError`。
- `test_mention_is_prepended` — `<!here>` が本文先頭に付く。
- `test_empty_mention_leaves_text_unchanged`
- `test_blocks_are_forwarded_in_bot_mode`
- `test_bot_api_error_raises` — 応答 `{"ok": false, "error": "channel_not_found"}` で例外。**例外メッセージにトークンが含まれないこと**を assert する。
- `test_token_is_never_logged` — ログ出力（caplog）にトークン文字列が現れない。

**手順:** 流用元 2 ファイルを読む → テスト作成 → 失敗確認 → 実装 → 全 passed → CRLF 変換・確認 → `ruff check infra/` → コミット（`mod refs #1001 notifier の SES 送信と Slack 投稿を追加`）。

**受け入れ条件:** 16 件 passed。秘密（トークン・Webhook URL）がログ・例外メッセージに出ない。CRLF。

---

### Task P2-3: notifier のハンドラ（SQS / 冪等性 / 部分失敗）

**目的:** SQS からメッセージを受け、チャンネル単位で冪等に送信する（設計 8.1 / 8.6）。

**Files:**
- Create: `infra/core/src/notifier/app.py`
- Create: `infra/core/tests/test_notifier_handler.py`

**流用元:** `/mnt/c/Users/devA/git/ref-project-a/functions/notifier/app.py` — `example_powertools` の import フォールバック、相対 import の二重化（`try: from ses_sender import ...` / `except ImportError: from notifier.ses_sender import ...`）、通知先ごとの try/except で 1 件失敗しても続行する流れ。**ただし本件は `ReportBatchItemFailures` を使う点が異なる。**

**公開インターフェース:**
- `@LambdaLogger.contextualize def handler(event: dict, context) -> dict` — SQS イベント。戻り値は `{"batchItemFailures": [{"itemIdentifier": <messageId>}, ...]}`
- `def process_record(body: dict) -> None` — 1 メッセージ分。`channels` を順に処理する。
- `def validate_message(body: dict) -> None` — 受信側の軽量検証。`schema` が `example-backoffice.notify.v1` でない、必須項目が欠ける等は `PermanentError`。
- `class PermanentError(Exception)` — 恒久エラー（再試行しても直らない）。`AttachmentNotFoundError` / `SlackNotConfiguredError` / スキーマ不一致がこれに当たる。
- `def send_to_dlq(body: dict, reason: str) -> None` — 恒久エラー時に notifier 自身が DLQ へ `sqs:SendMessage` する（設計 8.6）。環境変数 `DLQ_URL`。
- 冪等性: Powertools Idempotency。**キーは `{message_id}#{channel}`**（設計 8.1）。`DynamoDBPersistenceLayer(table_name=os.environ["IDEMPOTENCY_TABLE"])`、`IdempotencyConfig(event_key_jmespath=...)` ではなく、チャンネル単位の内部関数 `@idempotent_function(data_keyword_argument="payload")` で包む。TTL 24 時間はテーブル側の `TimeToLiveSpecification` で設定。
- 環境変数: `ENV`、`SES_FROM_ADDRESS`、`IDEMPOTENCY_TABLE`、`DLQ_URL`、`SSM_PREFIX`（`/example-backoffice/{env}`）、`LOG_LEVEL`、`POWERTOOLS_SERVICE_NAME`

**テストケース（`test_notifier_handler.py`。`moto` で DynamoDB / SQS / S3 / SSM、SES と Slack はスタブ）:**
- `test_sends_email_and_slack_for_both_channels` — 両方の sender が 1 回ずつ呼ばれ、`batchItemFailures` が空。
- `test_email_only_message_does_not_call_slack`
- `test_transient_error_returns_batch_item_failure` — SES が一時エラーで例外 → `batchItemFailures` に当該 `messageId` が入る。
- `test_permanent_error_is_not_retried_and_goes_to_dlq` — `AttachmentNotFoundError` → `batchItemFailures` は空、DLQ に 1 件入る。
- `test_schema_mismatch_goes_to_dlq` — `schema` が `...v2` → DLQ、`batchItemFailures` 空。
- `test_slack_not_configured_goes_to_dlq` — `SlackNotConfiguredError` → DLQ（前提と判断 7）。
- `test_email_success_slack_failure_does_not_resend_email_on_retry` — 1 回目で Slack 失敗 → 同じ `message_id` で再実行すると **email は再送されず** Slack だけ再試行される（`{message_id}#{channel}` 冪等キーの検証。設計 8.6）。
- `test_batch_size_one_processes_single_record`
- `test_multiple_records_report_only_failed_ones`
- `test_message_id_and_app_are_logged` — 構造化ログに `message_id` / `app` / `channel` が出る。**秘密は出ない。**
- `test_valid_message_from_notify_send_is_accepted` — Task P1-4 の `notify.build_message()` が作ったメッセージがそのまま受理される（**送信側と受信側の契約テスト**）。`shared/schemas/notify.v1.json` で両方を検証する。

**手順:** 流用元を読む → テスト作成 → 失敗確認 → 実装 → 全 passed → `uv run pytest infra/core/tests --cov=notifier --cov-fail-under=80` → CRLF 変換・確認 → `ruff check infra/` → コミット（`mod refs #1001 notifier の SQS ハンドラと冪等処理を追加`）。

**受け入れ条件:** 11 件 passed。契約テストが `shared/schemas/notify.v1.json` を参照している。カバレッジ 80% 以上。CRLF。

---

### Task P2-4: core テンプレート（1/2）— Cognito / PreSignUp / SSM 契約

**目的:** Cognito User Pool 一式を作り、`/cognito/*` の契約パラメータを SSM に書く（設計 3.1 / 4.2 / 6.1 / 6.3）。

**Files:**
- Create: `infra/core/template.yaml`（このタスクで Cognito 部分まで）
- Create: `tests/infra/test_core_template.py`（Cognito 分のテスト）

**流用元:** `/mnt/c/Users/devA/git/ref-project-a/stacks/auth/template.yaml`（**丸ごと参考にする**。User Pool / Client / `UserPoolTags` のマップ形式）と `/mnt/c/Users/devA/git/ref-project-a/template.yaml` の 55-73 行（**PreSignUp Lambda を User Pool と同じテンプレートに置く場合の `AWS::Lambda::Permission` の付け方**。本件は core 単一テンプレートなので `SourceArn: !GetAtt UserPool.Arn` で循環参照になる → **`LambdaConfig.PreSignUp` を持つ User Pool と Permission の間で循環しないよう、Permission の `SourceArn` に `!Sub 'arn:aws:cognito-idp:${AWS::Region}:${AWS::AccountId}:userpool/*'` を使うか、`AWS::Cognito::UserPool` に `!GetAtt PreSignUpFunction.Arn` を渡し Permission 側は `!GetAtt UserPool.Arn` を使う（この向きなら循環しない）**）。

**テンプレートのリソース:**
| 論理 ID | 型 | 要点 |
| --- | --- | --- |
| `UserPool` | `AWS::Cognito::UserPool` | `UserPoolName: ${ProjectName}-${Env}`、`UsernameAttributes: [email]`、`UsernameConfiguration.CaseSensitive: false`、`AutoVerifiedAttributes: [email]`、`AdminCreateUserConfig.AllowAdminCreateUserOnly: false`、`Policies.PasswordPolicy`（12 文字以上・大小英字・数字・記号すべて true）、`MfaConfiguration: OPTIONAL`、`EnabledMfas: [SOFTWARE_TOKEN_MFA]`、`AccountRecoverySetting`（`verified_email` のみ）、`Schema: [{Name: email, Required: true, Mutable: true}]`、`LambdaConfig.PreSignUp: !GetAtt PreSignUpFunction.Arn`、`UserPoolTier: LITE`、`DeletionProtection`（prod のみ `ACTIVE`。`Condition` で切り替え）、`UserPoolTags: {Project: !Ref ProjectName, Name: UserPool}` |
| `UserPoolClient` | `AWS::Cognito::UserPoolClient` | `GenerateSecret: false`、`ExplicitAuthFlows: [ALLOW_USER_SRP_AUTH, ALLOW_REFRESH_TOKEN_AUTH]`、`PreventUserExistenceErrors: ENABLED`、`IdTokenValidity: 60` / `AccessTokenValidity: 60`（分）、`RefreshTokenValidity: 8`（時間）、`TokenValidityUnits` を明示 |
| `AdminGroup` | `AWS::Cognito::UserPoolGroup` | `GroupName: admin`、`Precedence: 1` |
| `PreSignUpFunction` | `AWS::Serverless::Function` | `FunctionName: ${ProjectName}-core-pre-signup-${Env}`、`CodeUri: src/pre_signup/`、`Handler: app.handler`、`Runtime: python3.13`、`Layers` は書かない（依存ゼロ。`Globals.Function` に `Layers` を置かないため打ち消しは不要）、`Tags: {Project, Name: Function}` |
| `PreSignUpLogGroup` | `AWS::Logs::LogGroup` | `/aws/lambda/${PreSignUpFunction}`、`RetentionInDays: 365`、`Tags: [Project, Name: LogGroup]` |
| `PreSignUpPermission` | `AWS::Lambda::Permission` | `Principal: cognito-idp.amazonaws.com`、`SourceArn: !GetAtt UserPool.Arn` |
| `UserPoolIdParameter` / `UserPoolClientIdParameter` / `UserPoolIssuerParameter` | `AWS::SSM::Parameter` | `/${ProjectName}/${Env}/cognito/user-pool-id` / `.../client-id` / `.../issuer`（issuer は `!Sub 'https://cognito-idp.${AWS::Region}.amazonaws.com/${UserPool}'`） |

**Parameters:** `ProjectName`、`Env`、`LogLevel`（`AllowedValues: [DEBUG, INFO, WARNING, ERROR]`）、`ExamplePowertoolsLayerArn`、`PowertoolsLayerArn`、`HostedZoneId`、`MailDomain`（dev `backoffice-dev.example-cloud.com` / prod `backoffice.example-cloud.com`）。
**Conditions:** `IsProd: !Equals [!Ref Env, prod]`。
**Globals:** `Function: { Runtime: python3.13, Architectures: [x86_64], MemorySize: 256, Timeout: 30, Tags: { Project: !Ref ProjectName, Name: Function } }`。

**テストケース（`tests/infra/test_core_template.py`）:**
- `test_user_pool_uses_email_as_username` — `UsernameAttributes == ["email"]`、`CaseSensitive` が false。
- `test_user_pool_mfa_is_optional_totp_only` — `MfaConfiguration == "OPTIONAL"`、`EnabledMfas == ["SOFTWARE_TOKEN_MFA"]`、SMS 構成（`SmsConfiguration`）が無い。
- `test_password_policy_requires_12_chars_and_symbols` — `MinimumLength == 12`、4 項目すべて true。
- `test_self_signup_is_allowed` — `AllowAdminCreateUserOnly` が false。
- `test_pre_signup_trigger_is_wired` — `LambdaConfig.PreSignUp` があり、`AWS::Lambda::Permission` の `SourceArn` が User Pool を指す。
- `test_app_client_has_no_secret_and_srp_auth` — `GenerateSecret` false、`ExplicitAuthFlows` が `ALLOW_USER_SRP_AUTH` + `ALLOW_REFRESH_TOKEN_AUTH` の **2 つだけ**（`ALLOW_ADMIN_USER_PASSWORD_AUTH` / `ALLOW_USER_PASSWORD_AUTH` を含まない）。
- `test_only_one_user_pool_client_exists` — `AWS::Cognito::UserPoolClient` はちょうど 1 つ（**E2E 用の 2 つ目の App Client は作らない**。疎通確認は `scripts/get_id_token.py` の SRP 認証で行う。前提と判断 13）。
- `test_token_validity` — ID / アクセス 60 分、リフレッシュ 8 時間（`TokenValidityUnits` 込み）。
- `test_admin_group_exists` — `GroupName == "admin"`。
- `test_writes_three_cognito_ssm_parameters` — `/cognito/user-pool-id` / `client-id` / `issuer` の 3 本。
- `test_issuer_value_format` — `https://cognito-idp.${AWS::Region}.amazonaws.com/${UserPool}` の形。
- `test_deletion_protection_is_conditional_on_prod`

**手順:** 流用元を読む → テスト作成 → skip 確認 → テンプレート実装 → `cd infra/core && sam validate --lint` → `uv run pytest tests/infra -v`（**タグ規約テストを含む**）→ CRLF 変換・確認 → コミット（`mod refs #1001 core テンプレートに Cognito と PreSignUp を追加`）。

**受け入れ条件:** `sam validate --lint` が通る。上記 12 件 + タグ規約テストが passed。**タグ対応リソース全部に `Project` + `Name` タグがある。** CRLF。

---

### Task P2-5: core テンプレート（2/2）— SES / SQS / notifier / S3 / アラーム / samconfig

**目的:** 通知基盤と添付バケット、SES Identity と DNS、DLQ アラームを作り、`/notify/*` と `/attachments/bucket` の契約を SSM に書く（設計 3.1 / 4.2 / 8.1 / 8.4 / 11.2 / 12.4）。

**Files:**
- Modify: `infra/core/template.yaml`（追記）
- Create: `infra/core/samconfig.toml`
- Modify: `tests/infra/test_core_template.py`（追記）

**流用元:**
- `/mnt/c/Users/devA/git/ref-project-a/stacks/ses/template.yaml`（**丸ごと参考。56 行**）— `AWS::SES::EmailIdentity` + `MailFromAttributes.MailFromDomain: bounce.${domain}` + `AWS::Route53::RecordSetGroup` に DKIM CNAME 3 本（`!GetAtt EmailIdentity.DkimDNSTokenName1..3` / `...Value1..3`）、MX（`10 feedback-smtp.<region>.amazonses.com`）、SPF TXT（**`'"v=spf1 include:amazonses.com ~all"'` と二重引用が必須**）。**リージョンは `ap-northeast-1` に読み替える。** DMARC TXT（`_dmarc.{domain}`、`"v=DMARC1; p=quarantine"`）を追加する（設計 8.4）。
- `/mnt/c/Users/devA/git/ref-project-a/stacks/queue/template.yaml`（**丸ごと参考**）— キュー + DLQ + `RedrivePolicy.maxReceiveCount: 3` + `SqsManagedSseEnabled: true` + Outputs で Url/Arn 両方。
- `/mnt/c/Users/devA/git/ref-project-a/stacks/notifier/template.yaml` — SQS イベントソース（`Type: SQS` / `BatchSize: 1`）と最小権限ロール。**本件は `FunctionResponseTypes: [ReportBatchItemFailures]` を追加する。**
- `/mnt/c/Users/devA/git/ref-project-a/stacks/api/template.yaml` 151-264 行 — IAM をインラインポリシーで機能別に分ける書き方、`ses:FromAddress` の `Condition`。
- `/mnt/c/Users/devA/git/ref-project-a/samconfig.toml` — samconfig の形。

**追加するリソース:**
| 論理 ID | 型 | 要点 |
| --- | --- | --- |
| `NotifyQueue` | `AWS::SQS::Queue` | `QueueName: ${ProjectName}-notify-queue-${Env}`、`VisibilityTimeout: 360`、`MessageRetentionPeriod: 345600`（4 日）、`RedrivePolicy: {deadLetterTargetArn: !GetAtt NotifyDlq.Arn, maxReceiveCount: 3}`、`SqsManagedSseEnabled: true`、`Tags: [Project, Name: Queue]` |
| `NotifyDlq` | `AWS::SQS::Queue` | `MessageRetentionPeriod: 1209600`（14 日）、`Tags: [Project, Name: Queue]` |
| `NotifyDlqAlarm` | `AWS::CloudWatch::Alarm` | `ApproximateNumberOfMessagesVisible >= 1`、`AlarmActions: [!Ref OpsTopic]`、`TreatMissingData: notBreaching` |
| `OpsTopic` | `AWS::SNS::Topic` | 基盤担当への通知。`Subscription` は `OpsEmail` パラメータ（既定 `devA@example.co.jp`） |
| `NotifierFunction` | `AWS::Serverless::Function` | `FunctionName: ${ProjectName}-core-notifier-${Env}`、`CodeUri: src/notifier/`、`MemorySize: 512`、`Timeout: 60`、`Layers: [ExamplePowertoolsLayerArn, PowertoolsLayerArn]`、`Events.Sqs`（`Queue: !GetAtt NotifyQueue.Arn`、`BatchSize: 1`、`FunctionResponseTypes: [ReportBatchItemFailures]`）、環境変数（`ENV` `SES_FROM_ADDRESS` `IDEMPOTENCY_TABLE` `DLQ_URL` `SSM_PREFIX` `LOG_LEVEL` `POWERTOOLS_SERVICE_NAME`） |
| `NotifierLogGroup` | `AWS::Logs::LogGroup` | 保持 365 日 |
| `IdempotencyTable` | `AWS::DynamoDB::Table` | `TableName: ${ProjectName}-core-idempotency-${Env}`、`PK: id`（Powertools の既定）、`PAY_PER_REQUEST`、`TimeToLiveSpecification: {AttributeName: expiration, Enabled: true}`、`SSESpecification`、`Tags: [Project, Name: Table]` |
| `AttachmentsBucket` | `AWS::S3::Bucket` | `${ProjectName}-attachments-${Env}-${AWS::AccountId}`、`BucketEncryption: SSE-S3`、`PublicAccessBlockConfiguration` 全 true、`OwnershipControls: BucketOwnerEnforced`、`LifecycleConfiguration`: 既定 400 日で `Expiration`、ただし `RetainPrefixes`（`CommaDelimitedList`、既定 `saas-a-billing/`）のプレフィックスは除外ルールを別に定義、`Tags: [Project, Name: Bucket]` |
| `EmailIdentity` | `AWS::SES::EmailIdentity` | `EmailIdentity: !Ref MailDomain`、`MailFromAttributes.MailFromDomain: bounce.${MailDomain}` |
| `SesRecordSetGroup` | `AWS::Route53::RecordSetGroup` | DKIM CNAME × 3、MX、SPF TXT、DMARC TXT |
| `SesConfigurationSet` + `SesEventDestination` | `AWS::SES::ConfigurationSet` / `ConfigurationSetEventDestination` | バウンス・苦情を `OpsTopic` へ（設計 8.4） |
| `NotifyQueueUrlParameter` / `NotifyQueueArnParameter` / `AttachmentsBucketParameter` | `AWS::SSM::Parameter` | `/notify/queue-url` / `/notify/queue-arn` / `/attachments/bucket` |

**notifier の IAM（設計 8.1）:** `ses:SendEmail`（`Condition: ses:FromAddress`）、`s3:GetObject`（添付バケットのみ）、`ssm:GetParameter(s)`（`/example-backoffice/{env}/shared/slack/*` のみ）、`kms:Decrypt`（`alias/aws/ssm`）、DLQ への `sqs:SendMessage`、Idempotency テーブルへの `dynamodb:GetItem/PutItem/UpdateItem/DeleteItem`、自関数のロググループ。**`Resource: "*"` は Logs の作成のみ。**

**samconfig（`infra/core/samconfig.toml`。設計 13.2 / 14.1）:** セクション名まで含めて次の形にする。**`use_container` は `[*.build.parameters]`**、デプロイ設定は `[*.deploy.parameters]`。

```toml
version = 0.1

[default.build.parameters]
use_container = true

[default.deploy.parameters]
stack_name = "example-backoffice-core-dev"
region = "ap-northeast-1"
s3_bucket = "example-sam-ap-northeast-1"
s3_prefix = "example-backoffice-core-dev"
capabilities = "CAPABILITY_IAM"
confirm_changeset = false
tags = "Project=example-backoffice App=core Env=dev"
parameter_overrides = "ProjectName=example-backoffice Env=dev LogLevel=DEBUG MailDomain=backoffice-dev.example-cloud.com HostedZoneId=Z0EXAMPLEZONE0 ExamplePowertoolsLayerArn=arn:aws:lambda:ap-northeast-1:210000000001:layer:ExamplePowertoolsPython:86 PowertoolsLayerArn=<Powertools レイヤ ARN> OpsEmail=devA@example.co.jp RetainPrefixes=saas-a-billing/"

[production.build.parameters]
use_container = true

[production.deploy.parameters]
stack_name = "example-backoffice-core-prod"
region = "ap-northeast-1"
s3_bucket = "example-sam-ap-northeast-1"
s3_prefix = "example-backoffice-core-prod"
capabilities = "CAPABILITY_IAM"
confirm_changeset = true
tags = "Project=example-backoffice App=core Env=prod"
parameter_overrides = "ProjectName=example-backoffice Env=prod LogLevel=INFO MailDomain=backoffice.example-cloud.com HostedZoneId=Z0EXAMPLEZONE0 ExamplePowertoolsLayerArn=arn:aws:lambda:ap-northeast-1:210000000001:layer:ExamplePowertoolsPython:86 PowertoolsLayerArn=<Powertools レイヤ ARN> OpsEmail=devA@example.co.jp RetainPrefixes=saas-a-billing/"
```

`AWS_PROFILE` は書かない。`RetainPrefixes` は `CommaDelimitedList` なので値に空白を入れない。

**テストケース（`test_core_template.py` に追記）:**
- `test_notify_queue_has_dlq_with_max_receive_3`
- `test_notify_queue_visibility_timeout_is_360` — notifier の timeout 60 秒の 6 倍（設計 8.1）。
- `test_dlq_retention_is_14_days`
- `test_dlq_alarm_exists_and_targets_ops_topic`
- `test_notifier_uses_batch_size_1_and_report_batch_item_failures`
- `test_notifier_timeout_60_memory_512`
- `test_idempotency_table_has_ttl`
- `test_attachments_bucket_blocks_public_access` — 4 項目すべて true、`OwnershipControls` が `BucketOwnerEnforced`、暗号化あり。
- `test_attachments_lifecycle_expires_at_400_days`
- `test_ses_identity_has_mail_from_and_dkim_records` — CNAME 3 本 + MX + SPF TXT + DMARC TXT。
- `test_ses_mx_points_to_tokyo_region` — `feedback-smtp.ap-northeast-1.amazonses.com`。
- `test_writes_notify_and_attachments_ssm_parameters` — 3 本。
- `test_notifier_iam_has_no_wildcard_resource_except_logs`
- `test_notifier_ssm_permission_is_scoped_to_slack_prefix`

**手順:** 流用元 4 ファイルを読む → テスト追記 → 失敗確認 → テンプレート追記 + samconfig 作成 → `cd infra/core && sam validate --lint` → `uv run pytest tests/infra -v` → `sam build --use-container`（デプロイはしない）→ CRLF 変換・確認 → コミット（`mod refs #1001 core テンプレートに SES/SQS/notifier/添付バケットを追加`）。

**受け入れ条件:** `sam validate --lint` が通る。上記 14 件 + Cognito 分 11 件 + タグ規約テストが passed。**タグ対応リソース全部に `Project` + `Name`**（`OpsTopic` は `Name: Topic`、`NotifyDlqAlarm` は `Name: Alarm`、`SesConfigurationSet` は `Name: ConfigurationSet`。前提と判断 16）。**samconfig に `[default.build.parameters]`（`use_container = true`）/ `[default.deploy.parameters]` / `[production.*]` があり、`stack_name` = `example-backoffice-core-{env}`、`region = "ap-northeast-1"`、`s3_bucket = "example-sam-ap-northeast-1"`、`tags` に `App=core` と `Env`。** LogGroup は保持 365 日。CRLF。

---

## Phase 3: edge / スクリプト

### Task P3-1: CloudFront Function `edge-guard.js`（IP 許可リスト + SPA フォールバック）

**目的:** 社内 IP allowlist と SPA のディープリンク対応を、viewer-request の CloudFront Function 1 本で実現する（設計 7.1 / 5.4）。**CloudFront はビヘイビアごとに viewer-request 関数を 1 つしか関連付けられない**ため、IP 判定と SPA フォールバックを同じ関数にまとめる（前提と判断 18）。

**Files:**
- Create: `infra/edge/functions/edge-guard.js`
- Create: `infra/edge/functions/edge-guard.test.mjs`（`node --test`。frontend の Vitest とは独立させる）

**Spec:** `Documents/DESIGN.md` 7.1（関数の全文・403 応答の形・プレースホルダ規約）と 5.4（SPA フォールバックの規則）。**設計 7.1 のコードをそのまま実装する。**

**流用元:** `/mnt/c/Users/devA/git/ref-project-a/stacks/web/template.yaml` の **100〜131 行付近の `IpRestrictFunction`**（`AWS::CloudFront::Function`、`Runtime: cloudfront-js-2.0`、`FunctionCode: !Sub` で `event.viewer.ip` を `'${AllowedIps}'.split(',')` と突き合わせ、`- AllowedIps: !Join [',', !Ref AllowedIpAddresses]` を渡す形）。**ref-project-a は WAF を使っておらず、この関数方式が実績のある前例である。** 本件との差分は 3 点:

| # | 差分 | 内容 |
| --- | --- | --- |
| 1 | 完全一致 → **IPv4 CIDR 判定** | 流用元は `indexOf(clientIp) === -1` の文字列完全一致。本件は `203.0.113.10/32` のような CIDR を扱うため、プレフィックス長でマスクして比較する |
| 2 | **SPA フォールバックを同居** | 流用元は IP 判定のみ。本件は IP 判定を通過した request に対し、拡張子の無い非 `/api/` パスを `/index.html` に書き換える（旧 Task P3-2 のロジックをそのまま持ち込む） |
| 3 | 403 の本文を **JSON** に | 流用元は `body: { encoding: 'text', data: 'Access denied' }`。本件は `content-type: application/json` + `{"ok":false,"error":{"code":"IP_FORBIDDEN"}}`（shared の `response.error()` のエンベロープに形を揃える） |

**公開インターフェース:**
- `function handler(event)` — ランタイムは `cloudfront-js-2.0`。処理は 2 段。
  1. **IP 判定:** `event.viewer.ip` が `ALLOWED` のどの IPv4 CIDR にも含まれなければ 403 レスポンスオブジェクトを返す（`statusCode: 403`、`statusDescription: 'Forbidden'`、`headers['content-type'].value = 'application/json'`、`headers['cache-control'].value = 'no-store'`、`body: { encoding: 'text', data: '{"ok":false,"error":{"code":"IP_FORBIDDEN"}}' }`）。IPv4 として解釈できない値（IPv6 表記・欠損）も 403 にする（**fail-closed**）。
  2. **SPA フォールバック:** 通過した request のうち、`request.uri` が `/api/` で始まらず `.` を含まないものだけ `/index.html` に書き換える。それ以外は `request` をそのまま返す。
- **許可 CIDR はプレースホルダで持つ。** `.js` 側は `var ALLOWED = '__ALLOWED_IPS__'.split(',').map(...)`、テンプレート側（Task P3-3）は同じ行を `var ALLOWED = '${AllowedIps}'.split(',').map(...)` として `FunctionCode: !Sub` の第 2 要素 `- AllowedIps: !Join [',', !Ref AllowedIpAddresses]` で埋める。一致検査テスト（P3-3）は `__ALLOWED_IPS__` → `${AllowedIps}` の 1 語だけ置換してから空白を正規化して比較する。
- **`.js` に `${` を書かない。** テンプレートへインライン展開したときに `!Sub` が解釈してしまうため（`${AllowedIps}` 以外の `${...}` があるとデプロイが失敗する）。
- テストのために `.js` の末尾へ `export { handler }` は付けない（CloudFront Function は ESM 非対応）。テスト側は `fs.readFileSync` で読み、`__ALLOWED_IPS__` をテスト用 CIDR に置換してから `vm.runInNewContext` で評価する。

**テストケース（`node --test infra/edge/functions/edge-guard.test.mjs`）:** 許可 CIDR は `203.0.113.10/32,192.0.2.0/24` を注入して検証する。

IP 判定（7 件）
- `203.0.113.10` からの `/` → 403 にならず `/index.html` へ書き換わる
- `198.51.100.1` からの `/` → 403（`statusCode` / `content-type: application/json` / 本文が `{"ok":false,"error":{"code":"IP_FORBIDDEN"}}`）
- `198.51.100.1` からの `/api/saas-a-billing/runs` → 403（**API 経路も遮断される**）
- `192.0.2.0` と `192.0.2.255` → 通過（`/24` の両端）
- `192.0.1.255` と `192.0.3.0` → 403（隣接レンジ）
- `203.0.113.11` → 403（`/32` の隣）
- `2001:db8::1` / `""` / `999.1.1.1` → 403（fail-closed）

SPA フォールバック（許可 IP から。9 件）
- `/saas-a-billing` → `/index.html`
- `/saas-a-billing/settings` → `/index.html`
- `/` → `/index.html`
- `/assets/index-abc123.js` → 変更なし（`.` を含む）
- `/index.html` → 変更なし
- `/favicon.ico` → 変更なし
- `/api/saas-a-billing/runs` → 変更なし（`/api/` で始まる）
- `/api/saas-a-billing/runs/2026-10/download` → 変更なし
- 通過時の戻り値が `event.request` と同一オブジェクトであること

プレースホルダ（1 件）
- `.js` の原文に `__ALLOWED_IPS__` がちょうど 1 箇所あり、`${` を含まないこと

**手順:** テスト作成 → `node --test infra/edge/functions/edge-guard.test.mjs` で失敗確認 → 実装 → 全 passed → **`.js` と `.test.mjs` を CRLF に変換する**（`.sh` ではないため）→ `file` で確認 → コミット（`mod refs #1001 IP 許可リストと SPA フォールバックの CloudFront Function を追加`）。

**受け入れ条件:** 17 件（IP 判定 7 + SPA フォールバック 9 + プレースホルダ 1）passed。`.js` と `.test.mjs` が CRLF。`.js` に `${` が無い。

---

### Task P3-2: （Task P3-1 に統合済み）

旧「SPA フォールバック CloudFront Function」。CloudFront はビヘイビアごとに viewer-request 関数を 1 つしか関連付けられないため、IP 判定と同じ関数 `infra/edge/functions/edge-guard.js` に統合した（前提と判断 18）。

**このタスクは実施しない。** 内容は Task P3-1 を読むこと。**番号は後続タスク・台帳・Self-Review の参照を壊さないため詰めない。**

---

### Task P3-3: edge スタック（S3 + OAC + CloudFront + Route53 + ログ）

**目的:** サイトの入口を作る（設計 5.1 / 5.2 / 5.4 / 5.5 / 12.3 / 12.4）。アプリ追加時に origin + behavior を 1 組足すだけで済む構造にする。

**Files:**
- Create: `infra/edge/template.yaml`, `infra/edge/samconfig.toml`
- Create: `tests/infra/test_edge_template.py`

**流用元:** `/mnt/c/Users/devA/git/ref-project-a/stacks/web/template.yaml`（**149 行を丸ごと参考**）— `AWS::CloudFront::OriginAccessControl`（`SigningBehavior: always` / `SigningProtocol: sigv4`）、S3 バケットポリシー（`Principal: cloudfront.amazonaws.com` + `Condition.StringEquals.AWS:SourceArn`）、`DistributionConfig`（`http2and3` / `PriceClass_200` / `DefaultRootObject: index.html`）、Route53 A エイリアス（`AliasTarget.HostedZoneId: Z2FDTNDATAQYW2`）。
**あわせて流用する:** 同ファイル 100〜131 行の `IpRestrictFunction`（`AWS::CloudFront::Function` + `FunctionCode: !Sub` + `- AllowedIps: !Join [',', !Ref AllowedIpAddresses]`）の**書き方**。関数の中身は Task P3-1 の `edge-guard.js` を使う。
**流用しない点:** `CustomErrorResponses`（設計 5.4 で禁止）。

**テンプレートのリソース:**
| 論理 ID | 型 | 要点 |
| --- | --- | --- |
| `SpaBucket` | `AWS::S3::Bucket` | `${ProjectName}-spa-${Env}-${AWS::AccountId}`、SSE-S3、パブリックブロック全 true、`OwnershipControls: BucketOwnerEnforced`、`Tags: [Project, Name: Bucket]` |
| `SpaBucketPolicy` | `AWS::S3::BucketPolicy` | OAC 経由のみ（`AWS:SourceArn` にディストリビューション ARN） |
| `CloudFrontLogBucket` | `AWS::S3::Bucket` | 標準ログ用。`OwnershipControls.Rules[0].ObjectOwnership: BucketOwnerPreferred`（**レガシー標準ログは配信先バケットの ACL 書き込みを要求するため。設計 11.2 の `BucketOwnerEnforced` に対する唯一の例外。前提と判断 14**）、`BucketEncryption: SSE-S3`、`PublicAccessBlockConfiguration` は 4 項目すべて true のまま、ライフサイクル 365 日で `Expiration`、`Tags: [Project, Name: Bucket]`（前提と判断 9） |
| `Oac` | `AWS::CloudFront::OriginAccessControl` | `OriginAccessControlOriginType: s3` |
| `EdgeGuardFunction` | `AWS::CloudFront::Function` | `Runtime: cloudfront-js-2.0`、`AutoPublish: true`、`FunctionCode: !Sub` で `infra/edge/functions/edge-guard.js` と同一内容をインライン展開する（`__ALLOWED_IPS__` の行だけ `${AllowedIps}` に置換し、第 2 要素に `AllowedIps: !Join [',', !Ref AllowedIpAddresses]` を渡す）。`Tags: [Project, Name: EdgeFunction]`（**`AWS::CloudFront::Function` はタグ対応**） |
| `Distribution` | `AWS::CloudFront::Distribution` | 下記参照。`Tags` は**トップレベル**（`DistributionConfig` の外）にリスト形式で `[Project, Name: Distribution]` |
| `DnsRecords` | `AWS::Route53::RecordSetGroup` | **A の Alias のみ**（`Z2FDTNDATAQYW2`）、`HostedZoneId: !Ref HostedZoneId`。**AAAA は作らない**（`IPV6Enabled: false` かつ IP 判定が IPv4 のみのため。設計 7.1） |
| `ApiDomainOutput` 等 | `Outputs` | `DistributionId`、`DistributionDomainName`、`SpaBucketName`、`SiteUrl` |

**`DistributionConfig` の要点（設計 5.2）:**
- `Aliases: [!Ref SiteDomain]`、`ViewerCertificate: {AcmCertificateArn: !Ref AcmCertificateArn, SslSupportMethod: sni-only, MinimumProtocolVersion: TLSv1.2_2021}`
- **`WebACLId` は書かない**（WAF 廃止。前提と判断 18）
- `IPV6Enabled: false`（**必須**。IP 判定が IPv4 のみのため、IPv6 で到達されると素通りする。設計 7.1）
- `DefaultCacheBehavior`: TargetOrigin = S3、`ViewerProtocolPolicy: https-only`、`AllowedMethods: [GET, HEAD]`、`CachePolicyId: 658327ea-f89d-4fab-a63d-7e88639e58f6`（CachingOptimized）、`ResponseHeadersPolicyId: 67f7725c-6f97-4210-82d7-5512b31e9d03`（マネージド SecurityHeadersPolicy）、`FunctionAssociations: [{EventType: viewer-request, FunctionARN: !GetAtt EdgeGuardFunction.FunctionARN}]`、`Compress: true`
- `CacheBehaviors`: アプリごとに 1 本。第一弾は `/api/saas-a-billing/*`。`TargetOriginId: api-saas-a-billing`、`ViewerProtocolPolicy: https-only`、`AllowedMethods: [GET, HEAD, OPTIONS, PUT, PATCH, POST, DELETE]`（**7 メソッド必須**）、`CachePolicyId: 4135ea2d-6df8-44a3-9df3-4b5a84be39ad`（CachingDisabled）、`OriginRequestPolicyId: b689b0a8-53d0-40ab-baf2-68738e2966ac`（AllViewerExceptHostHeader。**`AllViewer` にすると 403**）、`FunctionAssociations: [{EventType: viewer-request, FunctionARN: !GetAtt EdgeGuardFunction.FunctionARN}]`（**default と同じ関数を必ず付ける。付け忘れるとそのアプリの API だけ IP 制限を素通りする**）、`Compress: true`
- `Origins`: S3（OAC）と API（`DomainName: !Ref SaasABillingApiDomain`、`CustomOriginConfig: {OriginProtocolPolicy: https-only, OriginSSLProtocols: [TLSv1.2]}`、`OriginCustomHeaders: [{HeaderName: x-origin-verify, HeaderValue: !Ref OriginVerifySecret}]`）
- `Logging`: `{Bucket: !Sub '${CloudFrontLogBucket}.s3.amazonaws.com', IncludeCookies: false, Prefix: cloudfront/}` — **レガシー標準ログの `Bucket` は `{bucket}.s3.amazonaws.com` 形式が第一候補**（AWS ドキュメントの表記）。これで配信されない場合の代替として `!GetAtt CloudFrontLogBucket.RegionalDomainName`（`{bucket}.s3.{region}.amazonaws.com`）を試し、**どちらを採ったかを報告する**
- **`CustomErrorResponses` は書かない**（設計 5.4）

**Parameters:** `ProjectName`、`Env`、`SiteDomain`、`HostedZoneId`、`AcmCertificateArn`、`AllowedIpAddresses`（`Type: CommaDelimitedList`、`Default: 203.0.113.10/32`）、`OriginVerifySecret`（`Type: String`、`NoEcho: true`）、`SaasABillingApiDomain`（`Type: AWS::SSM::Parameter::Value<String>`、`Default: /example-backoffice/dev/apps/saas-a-billing/api-domain`）。
**Conditions:** なし。

**samconfig（`infra/edge/samconfig.toml`。設計 13.2 / 14.1）:** このスタックも Lambda コードを持たない（CloudFront Function はインライン）ため `build` セクションは作らない。

```toml
version = 0.1

[default.deploy.parameters]
stack_name = "example-backoffice-edge-dev"
region = "ap-northeast-1"
s3_bucket = "example-sam-ap-northeast-1"
s3_prefix = "example-backoffice-edge-dev"
capabilities = "CAPABILITY_IAM"
confirm_changeset = false
tags = "Project=example-backoffice App=edge Env=dev"
parameter_overrides = "ProjectName=example-backoffice Env=dev SiteDomain=backoffice-dev.example-cloud.com HostedZoneId=Z0EXAMPLEZONE0 AcmCertificateArn=arn:aws:acm:us-east-1:210000000001:certificate/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee AllowedIpAddresses=203.0.113.10/32"

[production.deploy.parameters]
stack_name = "example-backoffice-edge-prod"
region = "ap-northeast-1"
s3_bucket = "example-sam-ap-northeast-1"
s3_prefix = "example-backoffice-edge-prod"
capabilities = "CAPABILITY_IAM"
confirm_changeset = true
tags = "Project=example-backoffice App=edge Env=prod"
parameter_overrides = "ProjectName=example-backoffice Env=prod SiteDomain=backoffice.example-cloud.com HostedZoneId=Z0EXAMPLEZONE0 AcmCertificateArn=arn:aws:acm:us-east-1:210000000001:certificate/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee AllowedIpAddresses=203.0.113.10/32 SaasABillingApiDomain=/example-backoffice/prod/apps/saas-a-billing/api-domain"
```

**prod は `SaasABillingApiDomain` を prod のキーに上書きする**（dev はテンプレートの `Default` を使う）。`OriginVerifySecret` だけは `deploy-edge.sh` が実行時に `--parameter-overrides` で渡すため samconfig に書かない（`AllowedIpAddresses` は秘密ではないので samconfig に書く。**IP の追加はこの行を直して `bash scripts/deploy-edge.sh {env}`**）。**ただし `sam deploy --parameter-overrides` はコマンドライン値が samconfig の同名キーを置き換える**ので、`deploy-edge.sh` は samconfig の `parameter_overrides` を読み出して連結してから渡す（Task P3-4 の表を参照）。

**テストケース:**
- `test_no_custom_error_responses` — `CustomErrorResponses` が存在しない（設計 5.4 の事故防止）。
- `test_default_behavior_uses_caching_optimized_and_edge_guard`
- `test_api_behavior_uses_caching_disabled_and_all_viewer_except_host`
- `test_api_behavior_allows_seven_methods`
- `test_api_origin_has_x_origin_verify_custom_header`
- `test_viewer_protocol_policy_is_https_only_everywhere` — default と全 behavior。
- `test_minimum_tls_is_1_2_2021`
- `test_spa_bucket_is_private_and_uses_oac`
- `test_bucket_policy_scopes_to_this_distribution` — `AWS:SourceArn` 条件がある。
- `test_distribution_has_no_web_acl` — `DistributionConfig` に `WebACLId` が無い（WAF 廃止。前提と判断 18）。
- `test_ipv6_is_disabled` — `DistributionConfig.IPV6Enabled` が `false`。
- `test_dns_records_have_no_aaaa` — `DnsRecords` の `RecordSets` に `Type: AAAA` が無い。
- `test_edge_guard_function_is_attached_to_both_behaviors` — default と `/api/saas-a-billing/*` の**両方**に viewer-request の `FunctionAssociations` があり、同じ `EdgeGuardFunction` を指す。
- `test_allowed_ips_parameter_default` — `Parameters.AllowedIpAddresses` が `CommaDelimitedList` で `Default` が `203.0.113.10/32`。
- `test_origin_verify_secret_parameter_is_no_echo`
- `test_cloudfront_function_code_matches_js_file` — テンプレートの `FunctionCode`（`!Sub` の第 1 要素）と `infra/edge/functions/edge-guard.js` の内容が、**`.js` 側の `__ALLOWED_IPS__` を `${AllowedIps}` に置換したうえで**（空白も正規化して）一致する。あわせて `!Sub` の第 2 要素に `AllowedIps: !Join [',', !Ref AllowedIpAddresses]` があることを検査する。
- `test_logging_is_enabled` — `DistributionConfig.Logging.Bucket` が `CloudFrontLogBucket` を参照し、`Prefix` が `cloudfront/`。
- `test_log_bucket_ownership_is_bucket_owner_preferred` — **ログ用バケットだけ** `BucketOwnerPreferred`（前提と判断 14）。
- `test_spa_bucket_ownership_is_bucket_owner_enforced` — SPA バケットは設計 11.2 どおり `BucketOwnerEnforced` のまま（例外がログ用バケットに閉じていることの検査）。
- `test_api_domain_parameter_default_matches_contract` — `/example-backoffice/dev/apps/saas-a-billing/api-domain`（設計 4.2）。

**手順:** 流用元を読む → テスト作成 → skip 確認 → 実装 → **WAF 廃止に伴う後片付け（下記）** → `cd infra/edge && sam validate --lint` → `uv run pytest tests/infra -v` → `bash scripts/validate-templates.sh` → CRLF 変換・確認 → コミット（`mod refs #1001 edge スタック（S3+OAC+CloudFront+Route53）を追加`）。

**WAF 廃止に伴う後片付け（このタスクでまとめて行う。前提と判断 18）:**
- [ ] `tests/infra/test_template_tags.py` の `TEMPLATES` から `"infra/edge-global/template.yaml"` を外す（残すと「テンプレートが無い」で全件 skip し続ける）。
- [ ] 同ファイルの `ALLOWED_NAME_VALUES` から `"WebACL"` を外し、`"EdgeFunction"` が入っていることを確認する（`AWS::CloudFront::Function` に付ける）。
- [ ] `scripts/validate-templates.sh` の `TEMPLATES` から `"infra/edge-global"` を外し、**`cfn-lint --regions us-east-1` の特例分岐も削除する**（全テンプレートが `ap-northeast-1` になる）。
- [ ] 空ディレクトリ `infra/edge-global/` が残っていれば削除する（`rmdir infra/edge-global`。git は空ディレクトリを追跡しないため差分には出ない）。

**受け入れ条件:** `sam validate --lint` が通る。20 件 + タグ規約テストが passed。**`Distribution` / 両 `Bucket` / `EdgeGuardFunction` に `Project` + `Name` タグ、samconfig の `stack_name` = `example-backoffice-edge-{env}`・`region = "ap-northeast-1"`・`tags` に `App=edge` と `Env`、`parameter_overrides` に `AllowedIpAddresses`。** `bash scripts/validate-templates.sh` が edge-global を参照しない。CRLF。

---

### Task P3-4: デプロイ・運用スクリプト

**目的:** 秘密の登録と、SSM / CloudFormation Outputs を人がコピーせずに受け渡すデプロイ手順をスクリプト化する（設計 5.5 / 13.3 / 13.4）。

**Files（すべて `.sh` = **LF**。`chmod +x` する）:**
- Create: `scripts/init-secrets.sh`, `scripts/register-slack.sh`, `scripts/deploy-edge.sh`, `scripts/deploy-frontend.sh`, `scripts/deploy-all.sh`, `scripts/list-mfa-status.sh`
- Create: `scripts/get_id_token.py`（**Python。`.sh` ではないので CRLF**。前提と判断 13）
- Create: `scripts/README.md`（各スクリプトの用途と引数。**`Documents/` 配下ではないので新規作成してよい**）

**流用元:** `/mnt/c/Users/devA/git/ref-project-a/scripts/generate-env.sh` — `aws cloudformation describe-stacks --query "Stacks[0].Outputs[?OutputKey=='X'].OutputValue" --output text` で Outputs を引く形。`/mnt/c/Users/devA/git/example-portal/scripts/register-ssm-params.sh` — 冪等な `put-parameter --overwrite`、`DRY_RUN=1` 対応。`/mnt/c/Users/devA/git/example-portal/apps/ui-lte-m/scripts/verify.sh` — `pass/fail` カウンタと `check()` ヘルパの書き方。

**各スクリプトの仕様:**

| スクリプト | 引数 | 動作 |
| --- | --- | --- |
| `init-secrets.sh` | `{env}` | `openssl rand -base64 48` で 32 バイト以上の乱数を作り `/example-backoffice/{env}/edge/origin-verify-secret` に `--type SecureString --overwrite` で登録。**値を標準出力に出さない**（`--overwrite` 時は確認プロンプトを出す）。`DRY_RUN=1` に対応 |
| `register-slack.sh` | `{env} bot` または `{env} webhook`（**値は引数で受け取らない**） | 値は必ず標準入力から `read -rsp 'Slack bot token: ' VALUE`（`webhook` なら `Webhook URL: `）で受け取る。空入力はエラー。引数が 3 つ以上あるときは **usage を出して終了コード 1**（履歴に秘密が残る使い方を封じる）。`bot` なら `/example-backoffice/{env}/shared/slack/bot-token` を `--type SecureString --overwrite` で登録し、続けて `/example-backoffice/{env}/shared/slack/mode` を `aws ssm put-parameter --type String --overwrite --value bot` で更新する。`webhook` なら `/shared/slack/webhook-url` を SecureString で登録（`mode` は触らない）。**値・トークンを標準出力にもログにも出さない**（`set +x`）。`DRY_RUN=1` 対応。**実行後に `shared/samconfig.toml` の `SlackMode` も `bot` へ直すよう案内を表示する**（次回の shared デプロイで `webhook` に戻るのを防ぐ） |
| `deploy-edge.sh` | `{env}` | (1) `aws ssm get-parameter --with-decryption` で origin-verify-secret を取得、(2) **samconfig の `parameter_overrides` を読み出して連結してから** `cd infra/edge && sam deploy [--config-env production] --parameter-overrides "$BASE OriginVerifySecret=..."` を実行する。**取得した値をログに出さない**（`set +x`）。**WAF 廃止により `describe-stacks` での WebAclArn 取得は行わない**（前提と判断 18）<br>**注意: `sam deploy --parameter-overrides` はコマンドラインの値が samconfig の同名キーを丸ごと置き換える（追記ではない）。** 素朴に `OriginVerifySecret` だけを渡すと `SiteDomain` / `HostedZoneId` / `AcmCertificateArn` / `AllowedIpAddresses` が消えてデプロイが壊れる。samconfig 側の値を次のように読み出して前置する（`jq` は使わない。`tomllib` は Python 3.11+ 標準）:<br>`BASE=$(python3 -c 'import tomllib,sys; d=tomllib.load(open(sys.argv[1],"rb")); print(d[sys.argv[2]]["deploy"]["parameters"].get("parameter_overrides",""))' infra/edge/samconfig.toml "$CFG_ENV")`（`CFG_ENV` は dev なら `default`、prod なら `production`） |
| `deploy-frontend.sh` | `{env}` | (1) SSM から `/cognito/user-pool-id` と `/cognito/client-id` を取得、(2) `cd frontend && npm ci && VITE_COGNITO_USER_POOL_ID=... VITE_COGNITO_CLIENT_ID=... VITE_ENV={env} npm run build`、(3) edge スタックの Outputs から `SpaBucketName` / `DistributionId` を取得、(4) `aws s3 sync dist/ s3://{bucket}/ --delete`、(5) `aws s3 cp dist/index.html s3://{bucket}/index.html --cache-control no-cache --content-type text/html --metadata-directive REPLACE`、(6) `aws cloudfront create-invalidation --paths "/*"` |
| `deploy-all.sh` | `{env}` | `core` → `shared` → `apps/*`（`template.yaml` があるもの）→ `deploy-edge.sh` → `deploy-frontend.sh` の順（設計 3.3）。各ステップ前に `sam build --use-container` |
| `get_id_token.py` | `{env} {email} [--totp-code 123456]` | **E2E 用 App Client は追加しない**（前提と判断 13）。設計 6.1 の SPA クライアントは `ALLOW_USER_SRP_AUTH` + `ALLOW_REFRESH_TOKEN_AUTH`・シークレット無しなので、**`pycognito`（dev 依存）の SRP 認証**でそのまま使える。処理: (1) `boto3` で `/example-backoffice/{env}/cognito/user-pool-id` と `/cognito/client-id` を SSM から取得、(2) パスワードは環境変数 `COGNITO_PASSWORD` があればそれ、無ければ `getpass.getpass()` で対話取得（**引数では受け取らない**）、(3) `pycognito.Cognito(user_pool_id, client_id, username=email).authenticate(password)`、(4) `SOFTWARE_TOKEN_MFA` チャレンジが返ったら `--totp-code` の値（無ければ `input()`）で `respond_to_software_token_mfa()`、(5) `id_token` だけを標準出力へ 1 行で出す（`access_token` / `refresh_token` は出さない）。`{env}` は `dev` / `prod` 以外を弾く。実行例: `ID_TOKEN=$(uv run python scripts/get_id_token.py dev devA@example.co.jp)` |
| `list-mfa-status.sh` | `{env}` | `list-users` → 各ユーザーに `admin-get-user` して `UserMFASettingList` が空のユーザーを列挙（設計 6.4 の段階 3 の確認用） |

**共通の作法:** すべて `#!/usr/bin/env bash` + `set -euo pipefail`。`AWS_PROFILE` は環境変数から。`{env}` は `dev` / `prod` 以外を弾く。`prod` 対象の破壊的操作（`--overwrite` を伴う SSM 登録、`s3 sync --delete`）は実行前に `read -p` で確認する。

**ローカルに `jq` も `shellcheck` も入っていない（実測: `which jq shellcheck` が何も返さない）。** したがって **スクリプト本体で `jq` を使わない**。JSON の取り出しは以下のどちらかで行う。

```bash
# 1) AWS CLI の --query + --output text（第一候補）
SPA_BUCKET=$(aws cloudformation describe-stacks \
  --stack-name "example-backoffice-edge-${ENV}" \
  --query "Stacks[0].Outputs[?OutputKey=='SpaBucketName'].OutputValue" --output text)

# 2) --query で書けない整形だけ python3 で（jq の代わり）
python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["Parameter"]["Value"])'
```

**テスト:** シェルスクリプトのユニットテストは作らない。代わりに以下を確認する。
- [ ] `bash -n <script>` で全スクリプトの構文チェックが通る。
- [ ] `python3 -m py_compile scripts/get_id_token.py` が通る。`uv run ruff check scripts/` も通す。
- [ ] `grep -n 'jq' scripts/*.sh` が**何も返さない**（`jq` 未インストールのため使わない）。
- [ ] `shellcheck` は未インストール。`command -v shellcheck >/dev/null && shellcheck scripts/*.sh || echo 'shellcheck 未インストールのためスキップ'`（**あれば実行、無ければスキップして報告**）。
- [ ] `DRY_RUN=1 bash scripts/init-secrets.sh dev` が AWS を呼ばずに終わる。
- [ ] `bash scripts/deploy-edge.sh xxx` が不正な env で終了コード 1。
- [ ] `bash scripts/register-slack.sh dev bot extra-arg` が usage を出して終了コード 1（値を引数で渡せない）。

**手順:** 流用元 3 ファイルを読む → 実装 → `chmod +x`（`.sh` のみ）→ 上記の確認 → **`file scripts/*.sh` で全部が LF（CRLF が付いていないこと）を確認** → `scripts/README.md` と `scripts/get_id_token.py` は CRLF に変換 → コミット（`mod refs #1001 デプロイ・運用スクリプトを追加`）。

**受け入れ条件:** 全スクリプトが `bash -n` を通る。`.sh` が LF、`README.md` と `get_id_token.py` が CRLF。秘密（トークン・Webhook URL・パスワード・origin-verify）を引数・標準出力・ログに出さない。`jq` を使っていない。`shellcheck` は「あれば実行／無ければスキップ」で結果を報告する。

---

## Phase 4: frontend シェル

### Task P4-1: アプリレジストリ（`types.ts` / `registry.ts`）

**目的:** `frontend/src/apps/{app}/index.ts` を `import.meta.glob` で自動登録する（設計 9.4）。アプリ追加でシェル側のファイルを触らなくて済む構造の中核。

**Files:**
- Create: `frontend/src/apps/types.ts`, `frontend/src/apps/registry.ts`, `frontend/src/apps/registry.test.ts`
- Delete: `frontend/src/smoke.test.ts`（Task P0-2 の仮テスト）

**Spec:** 設計 9.4（`AppManifest` の型定義。**そのまま実装する**）、9.1（予約パス）。

**公開インターフェース:**
- `types.ts`: `interface AppNavItem { title; to; icon?; requiredGroups? }`、`interface AppManifest { id; title; icon; description?; order?; requiredGroups?; routes: RouteRecordRaw[]; nav: AppNavItem[] }`、`export function defineApp(manifest: AppManifest): AppManifest`
- `registry.ts`: `export const apps: AppManifest[]` — `import.meta.glob<{default: AppManifest}>(['./*/index.ts', '!./_*/index.ts'], { eager: true })` を `order`（既定 50）昇順 → `title` の `localeCompare(..., 'ja')` で整列。`export function findApp(id: string): AppManifest | undefined`。
- 定数 `RESERVED_PATHS = ['login','signup','mfa','forbidden','api','assets']`（設計 9.1）。

**テストケース（`registry.test.ts`。設計 9.4 の検証項目）:**
- `test: id が重複しない`
- `test: id が kebab-case（3〜30 文字）`
- `test: id が予約パスと衝突しない`
- `test: routes[].path が '/' で始まらない`（相対パスであること）
- `test: routes[].component がすべて関数（遅延ロード）`
- `test: _template がレジストリに含まれない`
- `test: order 昇順に並ぶ`（`order` 同値なら title の日本語順）
- `test: nav[].to が /{id} で始まる`
- `test: findApp が id で引ける / 未知の id は undefined`

**手順:** テスト作成 → `cd frontend && npm test` で失敗確認 → 実装 → 全 passed → `npm run lint` → `npm run build` → CRLF 変換・確認 → コミット（`mod refs #1001 フロントのアプリレジストリを追加`）。

**受け入れ条件:** 9 件 passed。`npm run build` が通る。CRLF。

---

### Task P4-2: Amplify 設定 / Vuetify プラグイン / main.ts

**目的:** SPA の起動経路を作る（設計 9.1 / 9.3 / 9.8）。日本語フォントとロケール、light テーマを明示する。

**Files:**
- Create: `frontend/src/config/amplify.ts`, `frontend/src/plugins/vuetify.ts`, `frontend/src/stores/ui.ts`
- Modify: `frontend/src/main.ts`（Task P0-2 の最小版を本実装に置き換え）
- Create: `frontend/src/config/amplify.test.ts`

**流用元:**
- `/mnt/c/Users/devA/git/example-portal/apps/sso-mock/web/src/config/amplify.ts`（**ほぼ無改変**。11 行）
- `/mnt/c/Users/devA/git/example-portal/apps/sso-mock/web/src/main.ts`（**ほぼ無改変**。`configureAmplify()` を `createApp` より**前**に実行する順序が要点）
- `/mnt/c/Users/devA/git/example-portal/apps/ui-point-setup/spa/src/config/vuetify.js` — 管理画面向けの `defaults`（`VTextField` / `VSelect` / `VAutocomplete` を `density: 'compact'`、`variant: 'outlined'`、`hideDetails: 'auto'`）。**sso-mock 版より実用的なのでこちらを採る。**

**公開インターフェース:**
- `config/amplify.ts`: `export function configureAmplify(): void` — `Amplify.configure({ Auth: { Cognito: { userPoolId: import.meta.env.VITE_COGNITO_USER_POOL_ID, userPoolClientId: import.meta.env.VITE_COGNITO_CLIENT_ID, loginWith: { email: true }, signUpVerificationMethod: 'code', userAttributes: { email: { required: true } } } } })`。環境変数が未設定なら `console.error` で警告し throw しない（テストで assert する）。
- `plugins/vuetify.ts`: `export default createVuetify({ locale: { locale: 'ja' }, theme: { defaultTheme: 'light' }, defaults: {...} })`。**`defaultTheme: 'light'` を明示する**（Vuetify 4 の既定は OS 追従。設計 9.1）。
- `stores/ui.ts`: `defineStore('ui', ...)` — `drawer`（開閉）、`rail`、`snackbar({text, color})` の表示。
- `main.ts`: `configureAmplify()` → `createApp(App)` → `createPinia()` → `vuetify` → `router` → `mount('#app')`。`@mdi/font/css/materialdesignicons.css` を import。
- 環境変数の型は `frontend/src/env.d.ts` の `ImportMetaEnv` に `VITE_COGNITO_USER_POOL_ID` / `VITE_COGNITO_CLIENT_ID` / `VITE_ENV` を追加する（流用元: `/mnt/c/Users/devA/git/example-portal/apps/sso-mock/web/src/vite-env.d.ts`）。

**テストケース（`amplify.test.ts`。`vi.mock('aws-amplify')`）:**
- `test: Amplify.configure に userPoolId と userPoolClientId が渡る`
- `test: loginWith.email が true`
- `test: signUpVerificationMethod が 'code'`
- `test: 環境変数が無いとき configure を呼ばずエラーログを出す`

**手順:** 流用元 3 ファイルを読む → テスト作成 → 失敗確認 → 実装（`App.vue` も作る。`<router-view />` のみ）→ 全 passed → `npm run lint` → `npm run build` → CRLF 変換・確認 → コミット（`mod refs #1001 Amplify 設定と Vuetify プラグインを追加`）。

**受け入れ条件:** 4 件 passed。`npm run build` が通る。フォントスタックに中華フォントが無い（`settings.scss` を確認）。CRLF。

---

### Task P4-3: 認証ストア（`stores/auth.ts`）

**目的:** Amplify v6 の `signIn` / `confirmSignIn` を `nextStep.signInStep` で分岐する状態機械を作る（設計 6.4 の SPA 側変更点。**MFA 必須化に備えて初期リリースで実装しておく**）。

**Files:**
- Create: `frontend/src/stores/auth.ts`, `frontend/src/stores/auth.test.ts`
- Create: `frontend/src/utils/authErrors.ts`, `frontend/src/test/setup.ts`
- Modify: `frontend/vitest.config.ts`（`setupFiles: ['./src/test/setup.ts']`）

**流用元:**
- `/mnt/c/Users/devA/git/example-portal/apps/sso-mock/web/src/stores/auth.ts` — **最も近い実装**。`AMPLIFY_ERROR_MESSAGES: Record<string,string>`（`NotAuthorizedException` 等 9 種 → 日本語）、`checkAuth()` が `getCurrentUser()` + `fetchAuthSession()` で `email` と `groups` を取る形、`getFreshIdToken()`。**監査ログ呼び出し（`logEvent`）は外す。**
- `/mnt/c/Users/devA/git/example-portal/apps/sso-mock/web/src/utils/authErrors.ts` — **無改変**。
- `/mnt/c/Users/devA/git/example-portal/apps/sso-mock/web/src/test/setup.ts` — **無改変**。`ResizeObserver` スタブ（Vuetify のマウントに必須）、`globalThis.crypto`、`vi.mock('aws-amplify/auth')` のグローバル適用。
- 分岐の仕様は `Documents/research/research_R2.md` 3.5 の `route()` 関数（**設計 6.4 の表と一致している**）。

**公開インターフェース（setup ストア `defineStore('auth', () => {...})`）:**
- state: `email: Ref<string|null>`、`groups: Ref<string[]>`、`initialized: Ref<boolean>`、`isLoading`、`error: Ref<string|null>`、`pending: Ref<Pending>`
- `type Pending = { kind: 'totp-setup'; setupUri: string; sharedSecret: string } | { kind: 'totp-code' } | { kind: 'new-password' } | { kind: 'confirm-signup'; username: string } | null`
- getter: `isAuthenticated = computed(() => email.value !== null)`、`isAdmin = computed(() => groups.value.includes('admin'))`
- `async function checkAuth(): Promise<void>` — `getCurrentUser()` + `fetchAuthSession()`。`groups` は `tokens.idToken.payload['cognito:groups']`（配列でも文字列でも配列に正規化する）。失敗時は state をクリアし `initialized = true`。
- `async function login(username: string, password: string): Promise<void>` — `signIn()` の結果を `route()` に渡す。
- `async function submitChallenge(response: string, username: string): Promise<void>` — `confirmSignIn({ challengeResponse: response })` の結果を再度 `route()`。
- `async function logout(): Promise<void>`、`async function register(...)`、`async function confirmRegistration(...)`
- 内部 `async function route(result: SignInOutput, username: string): Promise<void>` — `DONE` / `CONTINUE_SIGN_IN_WITH_TOTP_SETUP`（`step.totpSetupDetails.getSetupUri('example-backoffice', username).toString()` と `sharedSecret` を `pending` に入れる）/ `CONFIRM_SIGN_IN_WITH_TOTP_CODE` / `CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED` / `CONFIRM_SIGN_UP` / `CONTINUE_SIGN_IN_WITH_MFA_SELECTION`（`confirmSignIn({challengeResponse:'TOTP'})` して再帰）/ その他は throw。
- `Hub.listen('auth')` の `tokenRefresh_failure` でストアをクリアする（設計 9.6）。

**テストケース（`auth.test.ts`。`vi.mocked(signIn)` で各 `signInStep` を返す）:**
- `test: DONE のとき pending が null になり checkAuth が呼ばれる`
- `test: CONFIRM_SIGN_IN_WITH_TOTP_CODE で pending.kind が 'totp-code'`
- `test: CONTINUE_SIGN_IN_WITH_TOTP_SETUP で setupUri と sharedSecret が入る`
- `test: CONFIRM_SIGN_IN_WITH_NEW_PASSWORD_REQUIRED で pending.kind が 'new-password'`
- `test: CONFIRM_SIGN_UP で pending.kind が 'confirm-signup' と username`
- `test: CONTINUE_SIGN_IN_WITH_MFA_SELECTION で TOTP を自動選択して再分岐する`
- `test: 未対応の signInStep は例外`
- `test: submitChallenge が confirmSignIn を呼び結果を再分岐する`
- `test: checkAuth が cognito:groups（配列）から groups を取る`
- `test: checkAuth が cognito:groups（文字列 "[admin]"）でも配列にする`
- `test: isAdmin が admin グループの有無で切り替わる`
- `test: NotAuthorizedException が日本語メッセージになる`
- `test: checkAuth 失敗で isAuthenticated が false かつ initialized が true`

**手順:** 流用元 3 ファイルを読む → テスト作成 → 失敗確認 → 実装 → 全 passed → `npm run lint` → `npm run build` → CRLF 変換・確認 → コミット（`mod refs #1001 認証ストア（signInStep 分岐）を追加`）。

**受け入れ条件:** 13 件 passed。設計 6.4 の 6 ステップすべてに分岐がある。CRLF。

---

### Task P4-4: ルーター / ガード / API クライアント

**目的:** アプリレジストリからルートを組み、認証とグループでガードする（設計 9.4 / 9.6 / 9.7）。

**Files:**
- Create: `frontend/src/router/index.ts`, `frontend/src/router/guards.ts`, `frontend/src/router/guards.test.ts`
- Create: `frontend/src/services/http.ts`, `frontend/src/services/http.test.ts`
- Create: `frontend/src/types/index.ts`（`ApiEnvelope<T>` / `ApiError`）

**流用元:**
- `/mnt/c/Users/devA/git/example-portal/apps/sso-mock/web/src/router/index.ts` — `meta` + `beforeEach` + 遅延 import の形。
- `/mnt/c/Users/devA/git/example-portal/apps/sso-mock/web/src/services/api.ts` — axios インスタンス + `fetchAuthSession()` の ID トークンを `Authorization: Bearer` に付けるインターセプタ、`extractApiError`。**`baseURL` を `createAppApi(appId)` で `/api/{appId}` にする点が本件の差分。**
- `/mnt/c/Users/devA/git/example-portal/apps/sso-mock/web/src/test/router/guard.test.ts` — `vi.resetModules()` → `await import('@/router/index')` でルータを作り直すテストの型。
- `/mnt/c/Users/devA/git/example-portal/apps/sso-mock/web/src/test/services/api.test.ts` — `await vi.importActual<typeof import('@/services/http')>(...)` でグローバルモックを回避するイディオム。
- ルート構成は設計 9.3 / 9.4 と `Documents/research/research_R2.md` 1.4。**ただし SPA ルートは `/{app}`（`/apps/{app}` ではない。設計 9.1）。**

**公開インターフェース（`router/index.ts`）:**
- ルート: `/login`（`meta.requiresAuth: false`, `layout: 'auth'`）、`/signup`（同）、`/`（`ShellLayout` の子として `''`=Home、`mfa`=MfaSetup、`forbidden`、および `apps.map(app => ({ path: '/'+app.id, meta: { appId, requiredGroups }, children: app.routes }))`）、`/:pathMatch(.*)*`=NotFound。
- すべて `component: () => import(...)`。

**公開インターフェース（`router/guards.ts`）:**
- `export function installGuards(router: Router): void`
- `beforeEach`: 初回のみ `auth.checkAuth()`。`to.matched.every(r => r.meta.requiresAuth !== false)` で要認証を判定 → 未認証なら `{ name: 'login', query: { redirect: to.fullPath } }`。認証済みで `/login`・`/signup` へ行こうとしたら `{ name: 'home' }`。`to.matched.flatMap(r => r.meta.requiredGroups ?? [])` を集め、1 つも所属しなければ `{ name: 'forbidden' }`。
- `afterEach`: `document.title = [to.meta.title, 'example-backoffice'].filter(Boolean).join(' | ')`。

**公開インターフェース（`services/http.ts`）:**
- `export function createAppApi(appId: string): AxiosInstance` — `baseURL: '/api/' + appId`、`timeout: 30000`。request インターセプタで `fetchAuthSession()` の **ID トークン**を `Authorization: Bearer` に付ける（設計 6.3）。response インターセプタで 401 なら `signOut()` して `/login?redirect=...` へ。
- `export function extractApiError(error: unknown): ApiError | null`
- `types/index.ts`: `interface ApiError { code: string; message: string }`、`interface ApiEnvelope<T> { ok: boolean; data: T | null; error: ApiError | null; meta: Record<string, unknown> | null }`（shared `response.py` と対応）。

**テストケース（`guards.test.ts`）:**
- `test: 未認証で保護ルートへ行くと /login?redirect= になる`
- `test: 認証済みで /login へ行くと / へリダイレクトされる`
- `test: requiresAuth: false のルートは未認証でも通る`
- `test: requiredGroups に所属していないと /forbidden`
- `test: requiredGroups に所属していれば通る`
- `test: 親ルートの requiredGroups が子ルートにも効く（to.matched の集約）`
- `test: afterEach で document.title が「{title} | example-backoffice」になる`
- `test: checkAuth は初回だけ呼ばれる`

**テストケース（`http.test.ts`）:**
- `test: baseURL が /api/{appId} になる`
- `test: リクエストに Authorization: Bearer <idToken> が付く`
- `test: トークンが取れないときは Authorization を付けない`
- `test: 401 応答で signOut が呼ばれ /login へ遷移する`
- `test: 401 以外はそのまま reject される`
- `test: extractApiError がエンベロープの error を取り出す`

**手順:** 流用元 4 ファイルを読む → テスト作成 → 失敗確認 → 実装 → 全 passed → `npm run lint` → `npm run build` → CRLF 変換・確認 → コミット（`mod refs #1001 ルーター・ガード・API クライアントを追加`）。

**受け入れ条件:** 14 件 passed。SPA ルートが `/{app}` 形式。CRLF。

---

### Task P4-5: レイアウトと共通ビュー

**目的:** サイドバー付きシェルと認証系画面を作る（設計 9.5 / 6.4）。

**Files:**
- Create: `frontend/src/layouts/ShellLayout.vue`, `AuthLayout.vue`
- Create: `frontend/src/components/TotpEnroll.vue`
- Create: `frontend/src/views/LoginView.vue`, `SignupView.vue`, `MfaSetupView.vue`, `HomeView.vue`, `ForbiddenView.vue`, `NotFoundView.vue`
- Create: `frontend/src/layouts/ShellLayout.test.ts`, `frontend/src/views/LoginView.test.ts`

**流用元:**
- `Documents/research/research_R2.md` 2.1 の `ShellLayout.vue`（`v-app` > `v-navigation-drawer`（デスクトップ permanent + rail、モバイル temporary）+ `v-app-bar`（`#extension` にマニフェストの `nav` を `v-tabs`）+ `v-main` > `v-container` > `<router-view>` を `keep-alive max=5` で包む）。
- `/mnt/c/Users/devA/git/example-portal/apps/sso-mock/web/src/views/LoginView.vue` — MFA / 新パスワード / 確認コードの分岐 UI と `v-form` + rules。**本件は `v-window` のステップとして同一ページ内で表現する**（設計 6.4。`signIn` → `confirmSignIn` はページのメモリ状態で繋がるため別ルートへ遷移しない）。
- `/mnt/c/Users/devA/git/example-portal/apps/sso-mock/web/src/components/layout/{ConfirmDialog,EnvBadge}.vue` — 無改変で流用可。

**公開インターフェース:**
- `ShellLayout.vue` — サイドバーに「ホーム」+ `apps`（`requiredGroups` で表示制御）+ 末尾に「セキュリティ（`/mfa`）」「ログアウト」。app-bar に現在のアプリ名とユーザーのメール。
- `TotpEnroll.vue` — props `{ setupUri: string; sharedSecret: string }`、emit `submit(code: string)`。`qrcode` パッケージで `otpauth://` URI を QR 化し、手入力用シークレットも表示する。**ログイン画面の TOTP 登録ステップと `/mfa` の両方で共有する**（設計 6.4）。
- `MfaSetupView.vue`（`/mfa`）— `setUpTOTP()` → `TotpEnroll` → `verifyTOTPSetup({ code })` → `updateMFAPreference({ totp: 'PREFERRED' })`。
- `LoginView.vue` — `v-window` の 5 ステップ（パスワード / TOTP コード / TOTP 登録 / 新パスワード / メール確認）。`auth.pending` の `kind` でステップを決める。
- `HomeView.vue` — `apps` をカードで並べ、`description` を出す。

**テストケース（`ShellLayout.test.ts`）:**
- `test: requiredGroups を満たすアプリだけサイドバーに出る`
- `test: 現在のアプリの nav が app-bar のタブに出る`
- `test: セキュリティとログアウトが末尾にある`

**テストケース（`LoginView.test.ts`。`auth` ストアをモックする）:**
- `test: 初期状態はパスワード入力ステップ`
- `test: pending.kind='totp-code' でコード入力ステップに切り替わる`
- `test: pending.kind='totp-setup' で QR（TotpEnroll）が出る`
- `test: pending.kind='new-password' で新パスワード入力が出る`
- `test: ログイン成功後 redirect クエリの先へ遷移する`

**手順:** 流用元を読む → テスト作成 → 失敗確認 → 実装 → 全 passed → `npm run lint` → `npm run build` → CRLF 変換・確認 → コミット（`mod refs #1001 シェルレイアウトと認証系ビューを追加`）。

**受け入れ条件:** 8 件 passed。`npm run build` が通る。独自 CSS で中華フォントを指定していない。CRLF。

---

### Task P4-6: アプリ雛形（frontend `_template` と `templates/app`）と E2E 土台

**目的:** `Documents/ADD-APP-GUIDE.md` 2 章・3 章が前提とする雛形を用意する。

**Files:**
- Create: `frontend/src/apps/_template/index.ts`, `routes.ts`, `api.ts`, `views/IndexView.vue`, `__tests__/index.test.ts`
- Create: `templates/app/cookiecutter.json`, `templates/app/{{cookiecutter.app_id}}/`（`template.yaml` / `samconfig.toml` / `functions/api/app.py` / `functions/requirements.txt` / `functions/batch/handlers/scheduled.py` / `statemachine/run.asl.json` / `tests/conftest.py` / `tests/test_health.py` / `edge-snippet.yaml` / `README.md`）
- Create: `frontend/playwright.config.ts`, `frontend/e2e/auth.setup.ts`, `frontend/e2e/stubs/auth.ts`, `frontend/e2e/fixtures/auth.fixture.ts`

**流用元:**
- `/mnt/c/Users/devA/git/example-portal/apps/sso-mock/web/playwright.config.ts` — **ほぼ無改変**（`webServer` は dev の CloudFront を使うなら外し、`baseURL` を `E2E_BASE_URL` 環境変数にする）。
- `/mnt/c/Users/devA/git/example-portal/apps/sso-mock/web/e2e/stubs/auth.ts` — **Cognito を叩かず localStorage に Amplify 形式トークンを注入する `seedAmplifyAuth()`** と `stubCognitoEndpoints()`。キー形式 `CognitoIdentityServiceProvider.{clientId}.{email}.{idToken|accessToken|refreshToken|clockDrift|signInDetails}` + `...LastAuthUser`。**ほぼ無改変で持ち込める。**
- `/mnt/c/Users/devA/git/example-portal/apps/sso-mock/web/e2e/fixtures/auth.fixture.ts` — ロール別の認証済み Page を fixture 化。
- app 雛形の `template.yaml` は **Task P2-4 / P2-5 で確定した書き方**と `Documents/ADD-APP-GUIDE.md` 3 章の表、設計 4.4 の受け口（`AWS::SSM::Parameter::Value<String>` 6 本）に従う。

**`templates/app/cookiecutter.json`:** `{"app_id": "sample-app", "app_title": "サンプル", "app_icon": "mdi-application"}`。

**`edge-snippet.yaml`（edge に転記する断片）には `FunctionAssociations`（viewer-request → `!GetAtt EdgeGuardFunction.FunctionARN`）を必ず含める。** 付け忘れるとそのアプリの API だけ社内 IP 制限を素通りする（設計 7.1、ADD-APP-GUIDE 3 章）。

**雛形 `template.yaml` に含めるもの（ADD-APP-GUIDE 3 章）:** HTTP API（`$default` ステージ、`DefaultAuthorizer` = Cognito JWT、`ANY /api/{{app_id}}/{proxy+}`、`AccessLogSettings`）、api Lambda（`Layers` は shared / Powertools / 社内レイヤ、`strip_prefixes`）、DynamoDB 単一テーブル、LogGroup（365 日）、`/apps/{{app_id}}/api-domain` を書く SSM パラメータ、`ScheduleV2`（コメントアウト）、`AWS::Serverless::StateMachine`（コメントアウト）、IAM（SQS SendMessage / S3 `{app}/*` / SSM `apps/{app}/*` + `/edge/origin-verify-secret` / DynamoDB 自テーブル / `states:StartExecution`）。**全リソースに `Project` + `Name` タグ、samconfig の `tags` に `Project=example-backoffice App={{app_id}} Env={env}`。**

**テストケース:**
- `frontend/src/apps/_template/__tests__/index.test.ts` — マニフェストが `defineApp()` で定義され、`id` / `routes` / `nav` の形が正しい。
- `frontend/src/apps/registry.test.ts`（Task P4-1）が `_template` を除外していることを再確認する。
- `tests/infra/test_app_template.py`（新規）— 雛形の `template.yaml` を（リポジトリルート相対ではないので `load_template` ではなく Task P0-4 の `_load_json_template(絶対パス)` で）読み、(a) `Parameters` の SSM 型 6 本の `Default` が設計 4.2 のキー名と一致、(b) `HttpApi` に `DefaultAuthorizer` がある、(c) 全リソースに `Project` + `Name` タグ、(d) LogGroup が 365 日、(e) `/apps/{app}/api-domain` を書く SSM パラメータがある。**cookiecutter のプレースホルダを含むため、テストは `{{cookiecutter.app_id}}` を `sample-app` に置換した一時ファイル（`/tmp` 配下）に対して行う。**

**手順:** 流用元 3 ファイルを読む → テスト作成 → 失敗確認 → 実装 → 全 passed → `cd frontend && npm test && npm run build` → `uv run pytest tests/infra -v` → **次のコマンドを `/tmp` で実行して生成物が `sam validate --lint` を通ることを確認し、生成物を削除する**（`--location` は相対パスだと cookiecutter が実行時カレント基準で解決して失敗するため**必ず絶対パス**にする）

```bash
cd /tmp && rm -rf /tmp/sample-app
sam init --no-input --name sample-app \
  --location /mnt/c/Users/devA/git/example-backoffice/templates/app \
  --extra-context '{"app_id":"sample-app","app_title":"サンプル"}'
cd /tmp/sample-app && sam validate --lint
rm -rf /tmp/sample-app
```

（`rm -rf` は `/tmp` 配下の生成物のみ。実行前にパスを目視確認する）
 → CRLF 変換・確認 → コミット（`mod refs #1001 アプリ雛形と E2E 土台を追加`）。

**受け入れ条件:** 雛形から生成したスタックが `sam validate --lint` を通る。タグ規約テストが通る。`npm test` / `npm run build` が通る。CRLF。

---

## Phase 5: dev デプロイと疎通確認

> **このフェーズは AWS を実際に変更する。** dev のみ。共有リソース（Route53 ゾーン / ACM / SES 送信枠 / `example-sam-*` バケット / `ExamplePowertoolsPython` レイヤ）は参照・新規追加のみ。**他プロジェクトのスタック・リソースに触れない。**

### Task P5-1: 秘密の登録 → core をデプロイ

**目的:** 契約レジストリ（SSM）を実在させる。

**Files:** なし（デプロイのみ）。結果は報告に記載する。

**手順:**
- [ ] `AWS_PROFILE=default aws sts get-caller-identity` でアカウントが `210000000001` であることを確認する。
- [ ] `bash scripts/init-secrets.sh dev` → `/example-backoffice/dev/edge/origin-verify-secret` が SecureString で登録される（**値は出力しない**）。
- [ ] `cd infra/core && sam build --use-container && sam deploy` → 成功を確認。
- [ ] SES の DNS 検証が完了することを確認する（数分かかる）。`aws sesv2 get-email-identity --email-identity backoffice-dev.example-cloud.com --query '{Verified:VerifiedForSendingStatus,Dkim:DkimAttributes.Status}'` が `Verified: true` / `Dkim: SUCCESS` になるまで待つ。
- [ ] 契約を確認する: `aws ssm get-parameters-by-path --path /example-backoffice/dev --recursive --query 'Parameters[].Name' --output text`。**`/cognito/user-pool-id` `/cognito/client-id` `/cognito/issuer` `/notify/queue-url` `/notify/queue-arn` `/attachments/bucket` `/edge/origin-verify-secret` の 7 本があること。**
- [ ] `UserPoolTier: LITE` が受理されたか確認する（要確認 #3）。拒否された場合はテンプレートから外して再デプロイし、**報告する**。
- [ ] コミットは不要（コード変更なし）。テンプレートを直した場合のみコミットする。

**受け入れ条件:** 上記 7 本の SSM キーが存在する。SES Identity が検証済み。`git status --short` が空。

---

### Task P5-2: shared → edge → frontend をデプロイ

**目的:** サイトを表示できる状態にする。

**手順:**
- [ ] `cd shared && sam build --use-container && sam deploy` → `/example-backoffice/dev/shared/layer-arn` と `/shared/slack/mode`（`webhook`）が書かれることを確認。
- [ ] **チェックポイント CP-1（必ず shared デプロイの「後」）:** ユーザーに Slack Bot token（または Incoming Webhook URL）の登録を依頼し、`bash scripts/register-slack.sh dev bot`（または `webhook`）を案内する。**値は引数ではなくプロンプトに貼ってもらう。** `/shared/slack/mode` は shared スタックが作るパラメータなので、**先行登録すると shared の CREATE が失敗する**（このデプロイの前に実行してはいけない）。`bot` を登録した場合は `shared/samconfig.toml` の `SlackMode=bot` に直してコミットし、`cd shared && sam deploy` で再適用する。**未登録のままでも先へ進んでよい**（Slack 送信テストは Task P5-3 で条件付き実施）。
- [ ] **saas-a-billing のアプリスタックは未作成**のため、edge の `SaasABillingApiDomain` パラメータが解決できない。**この段階では edge テンプレートの API origin / behavior と当該パラメータを一時的にコメントアウトしてデプロイし、SPA だけを配信する。**（saas-a-billing 計画の Task G-1 でコメントを外す。）
- [ ] **上のコメントアウトで `tests/infra/test_edge_template.py` の `test_api_domain_parameter_default_matches_contract` / `test_api_behavior_uses_caching_disabled_and_all_viewer_except_host` / `test_api_behavior_allows_seven_methods` / `test_api_origin_has_x_origin_verify_custom_header` / `test_edge_guard_function_is_attached_to_both_behaviors` の 5 件が落ちる。** テストを消さず、`@pytest.mark.xfail(reason="P5-2: saas-a-billing 未デプロイのため edge の API origin を一時コメントアウト中。saas-a-billing 計画 G-1 で解除する", strict=True)` を付けて **xfail のままコミットする**（`strict=True` にすることで、G-1 でコメントを外した瞬間に `XPASS` で落ち、マーカーの外し忘れに気づける）。G-1 では**マーカーを外して緑に戻す**ことを、saas-a-billing 計画側の作業として明記する。
- [ ] `bash scripts/deploy-edge.sh dev` → CloudFront ディストリビューションが作られ、Route53 に `backoffice-dev.example-cloud.com` が向く。**作成に 5〜10 分かかる。**
- [ ] `bash scripts/deploy-frontend.sh dev` → SPA が S3 に同期され invalidation が走る。
- [ ] `curl -sI https://backoffice-dev.example-cloud.com/` が **社内 IP から** 200 を返す。
- [ ] `git status --short` を確認。edge テンプレートのコメントアウトはコミットする（`mod refs #1001 saas-a-billing 未デプロイのため edge の API origin を一時無効化`）。

**受け入れ条件:** `https://backoffice-dev.example-cloud.com/` がログイン画面を返す。`/saas-a-billing` のようなディープリンクでも 200 で SPA が返る（CloudFront Function の確認）。`uv run pytest tests/infra -v` が **failed 0**（API 関連 5 件は `xfail`）で終わり、xfail の理由に「G-1 で解除」と書いてある。

---

### Task P5-3: 疎通確認（IP 制限 / 直叩き / 通知）

**目的:** 設計 5.5 / 7.1 / 8.x が意図どおり効いていることを実測する。

**手順:**
- [ ] **チェックポイント CP-3:** ユーザーに (a) 管理者ユーザーのサインアップと `admin` グループ追加、(b) E2E 用ユーザー（TOTP 未登録）の作成を依頼する。
- [ ] **サインアップ制限:** `@example.co.jp` 以外のアドレスでサインアップを試み、拒否されることを確認する（画面から。エラーメッセージが PreSignUp のもの）。
- [ ] **ログイン:** 管理者ユーザーでログインでき、サイドバーが表示されることを確認する。
- [ ] **`cognito:groups` の形式（要確認 #6）:** ログイン後に ID トークンをデコードし、`cognito:groups` が配列か `"[admin]"` 形式かを実測して**報告する**。
- [ ] **社内 IP から 200:** `curl -sI https://backoffice-dev.example-cloud.com/` が 200。
- [ ] **社外から 403（CP-4）:** 社内 IP 以外からのアクセスで CloudFront Function（`edge-guard`）が 403 を返すことを確認する。本文が `{"ok":false,"error":{"code":"IP_FORBIDDEN"}}`、`content-type` が `application/json` であることも見る。社外回線が使えない場合は、`infra/edge/samconfig.toml` の `AllowedIpAddresses` を一時的に別 IP に変更 → `bash scripts/deploy-edge.sh dev`（1〜2 分）→ 403 を確認 → 元に戻して再デプロイ、という手順で代替してよい（**変更・復元の両方を報告する**）。
- [ ] **API 直叩きが 403:** saas-a-billing 未デプロイのため API は無い。**Task G-1（saas-a-billing 計画）で `x-origin-verify` 無しの `execute-api` 直叩きが 403、ありで 200 になることを確認する**旨をここに記録しておく。
- [ ] **notifier のテスト送信:** `aws sqs send-message --queue-url $(aws ssm get-parameter --name /example-backoffice/dev/notify/queue-url --query Parameter.Value --output text) --message-body '<notify.v1 形式の JSON>'` を実行する。**宛先は `devA@example.co.jp` のみ。** `channels: ["email"]`、添付なしから始める。
  - [ ] メールが届くことを確認する（`From` が `example-backoffice <noreply@backoffice-dev.example-cloud.com>`）。
  - [ ] 添付ありのメッセージ（S3 に置いたダミー xlsx）も送り、添付が付くことを確認する。
  - [ ] 添付が S3 に無いメッセージを送り、**DLQ に 1 件入り、再試行されない**ことを確認する（設計 8.6）。
  - [ ] Slack が登録済み（CP-1 完了）なら `channels: ["email","slack"]` で投稿を確認する。**投稿先はテスト用チャンネルまたは CP-2 で指定されたチャンネルに限る。**
  - [ ] DLQ に入れたメッセージを削除（`purge-queue` ではなく個別削除）してアラーム状態を戻す。**purge は破壊的操作なのでユーザー確認を取る。**
- [ ] 実測値（`cognito:groups` の形式、IP 制限（`edge-guard`）の挙動、SES / Slack の結果）を**報告にまとめる**。
- [ ] `git status --short` が空であることを確認する。

**受け入れ条件:** 社内 IP から 200・社外から 403。`@example.co.jp` 以外のサインアップが拒否される。`devA@example.co.jp` 宛にメールが届く（添付あり／なし）。添付欠落メッセージが DLQ に入る。要確認 #6 の実測結果が報告されている。

---

## Self-Review（計画作成者による確認）

**設計書のカバー状況:**

| 設計章 | 対応タスク |
| --- | --- |
| 3.1 スタック一覧 | P1-5（shared）/ P2-4・P2-5（core）/ P3-3（edge） |
| 4.2 SSM キー一覧 | P1-5（`/shared/*`）/ P2-4（`/cognito/*`）/ P2-5（`/notify/*` `/attachments/bucket`）/ P3-4（`/edge/origin-verify-secret`）/ P5-1（実在確認） |
| 5.2 CloudFront behavior | P3-3 |
| 5.4 SPA フォールバック | P3-1（`edge-guard.js`）/ P3-3 |
| 5.5 x-origin-verify | P1-3（Lambda 側）/ P3-3（edge 側）/ P3-4（秘密の登録）/ saas-a-billing 計画 G-1（実測） |
| 6.1〜6.3 Cognito | P2-1 / P2-4 |
| 6.4 MFA 段階導入 | P4-3（`signInStep` 分岐）/ P4-5（UI）/ P3-4（`list-mfa-status.sh`） |
| 7.1・7.2 IP 制限（CloudFront Function） | P3-1（関数）/ P3-3（関連付け・`AllowedIpAddresses`） |
| 8.1〜8.6 通知基盤 | P1-1 / P1-4（送信側）/ P2-2 / P2-3（受信側）/ P2-5（インフラ） |
| 9.1〜9.8 フロント | P0-2 / P4-1〜P4-6 |
| 10.1〜10.4 shared | Phase 1 全体 |
| 11.1・11.2 データ | P2-5（Idempotency テーブル・添付バケット） |
| 12.1〜12.4 セキュリティ | P2-5（IAM）/ P3-3（TLS・OAC・セキュリティヘッダ）/ P0-4（LogGroup 365 日の機械検査） |
| 13.2・13.3 デプロイ | 各テンプレートタスクの samconfig / P3-4 / Phase 5 |
| 14.1・14.2 命名・タグ | P0-4（機械検査）＋ 全テンプレートタスクの受け入れ条件 |
| 15 テスト方針 | 全タスクが TDD。`sam validate --lint` は各テンプレートタスク、E2E は P4-6 |
| ADD-APP-GUIDE 3 章 雛形 | P4-6 |

**本計画で扱わないもの（設計 17 の要確認事項に対応）:** ユーザー管理 UI（`admin` アプリ）、CI/CD、MFA `ON` への切替、`x-origin-verify` の無停止ローテーション、prod へのデプロイ。

**インターフェースの一貫性:** `notify.send()` / `notify.build_message()`（P1-4）が作るメッセージを notifier（P2-3）が受理することを、両方のテストが `shared/schemas/notify.v1.json`（P1-1）で検証する。`auth.get_claims()`（P1-2）は HTTP API の `requestContext.authorizer.jwt.claims` を読む（REST API 形式ではない）。`createAppApi(appId)`（P4-4）の `baseURL` は `/api/{appId}` で、edge の behavior `PathPattern`（P3-3）と一致する。
