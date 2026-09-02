# アプリ追加手順（example-backoffice）

作成日: 2026-08-27
対象読者: example-backoffice に保守アプリを追加する社員
前提文書: [プラットフォーム設計書](DESIGN.md)

---

## 1. 前提知識

| 分野 | 必要な範囲 | 参照 |
| --- | --- | --- |
| AWS SAM | `sam build --use-container` / `sam deploy` / `samconfig.toml` の読み書き | DESIGN.md 13 章 |
| Lambda（Python 3.13） | Powertools `APIGatewayHttpResolver`、社内レイヤ ExamplePowertoolsPython の `LambdaLogger` | DESIGN.md 10 章 |
| SSM 契約レジストリ | `/example-backoffice/{env}/...` のキー一覧。値をコピーしない | DESIGN.md 4 章 |
| DynamoDB | 単一テーブル（`PK` / `SK`）の基本操作 | DESIGN.md 11 章 |
| Vue 3 + Vuetify 4 | `<script setup lang="ts">`、Vue Router の子ルート、Pinia | DESIGN.md 9 章 |
| Cognito | ID トークンの `cognito:groups`、`admin` グループの意味 | DESIGN.md 6 章 |
| テスト | pytest + moto、Vitest、Playwright | DESIGN.md 15 章 |

必要な環境: AWS CLI（`AWS_PROFILE` を環境変数で設定）、SAM CLI、cfn-lint 1.51.3 以上、Docker（`--use-container` 用）、Node.js 24、Python 3.13。

アプリ id の規約: 小文字英数とハイフン（kebab-case）、3〜30 文字。予約パス（`login` `signup` `mfa` `forbidden` `api` `assets`）は使えない。id は SPA ルート `/{app}`、API `/api/{app}`、スタック名 `example-backoffice-app-{app}-{env}` に使われる。

---

## 2. 手順（10 ステップ）

### ステップ 1: 設計書を書く

- `Documents/apps/{app}/DESIGN.md` を作成する。項目: 目的、業務ルール、機能要件、API 一覧、テーブル設計（PK / SK）、通知内容、スケジュール。
- 第一弾 `Documents/apps/saas-a-billing/DESIGN.md` を雛形にする。
- 図は mermaid-cli で SVG に変換して `Documents/apps/{app}/images/` に置き、`![図名](images/xxx.svg)` で参照する（Markdown 内の `mermaid` コードブロックは PDF 化で消えるため禁止。下書き段階の ASCII 図は可）。

### ステップ 2: バックエンド雛形を生成する

```bash
sam init --location ./templates/app --name {app} --no-input \
  --extra-context '{"app_id":"{app}","app_title":"表示名"}'
```

- `apps/{app}/` が生成される（3 章参照）。
- `apps/{app}/samconfig.toml` の `ExamplePowertoolsLayerArn` に社内レイヤの ARN を dev / prod それぞれ設定する（レイヤ ARN はテンプレートにハードコードしない）。

### ステップ 3: SSM 契約を確認する

- `apps/{app}/template.yaml` の `Parameters` の `Default` が DESIGN.md 4.2 のキー一覧と一致していることを確認する（雛形は一致した状態で生成される。キー名を変えない）。
- `samconfig.toml` の `[production.deploy.parameters]` が prod のキー名（`/example-backoffice/prod/...`）を上書きしていることを確認する。
- アプリ固有の秘密（外部 API の認証情報など）は `/example-backoffice/{env}/apps/{app}/...` に SecureString で手動登録する。`/apps/{app}/` 配下の階層はアプリが自由に決めてよい（例: saas-a-billing は `targets/{target_env}/base-url` のように集計対象環境で分けている）。

```bash
aws ssm get-parameters-by-path --path /example-backoffice/dev --recursive --query 'Parameters[].Name'
```

### ステップ 4: バックエンドを実装する（TDD）

1. `apps/{app}/tests/` にテストを書く（純関数 → handler の順）。`pytest` で失敗を確認する。
2. `apps/{app}/functions/api/app.py` にルートを追加する。`strip_prefixes=["/api/{app}"]`、`@origin_verify.origin_verify`、`@LambdaLogger.contextualize` は雛形に含まれている。全関数は `functions/` を 1 つの `CodeUri` として共有し、Handler だけを分ける。
3. 設定変更 API は `auth.require_group(claims, "admin")` で保護する。
4. 通知は `notify.send(...)`（shared レイヤ）。添付は `notify.put_attachment(app, filename, data, subprefix=None)` で `{app}/[{subprefix}/]{yyyy}/{mm}/{filename}` に置く。Slack は `slack.channel` にチャンネル **ID**（`#name` は不可）、`slack.mention` に先頭へ付ける文字列を渡す（DESIGN.md 8.2 / 8.5）。
5. バッチが必要なら `functions/batch/handlers/` にハンドラを置き、`ScheduleV2` イベント（雛形にコメントアウトで同梱）を有効にする。処理が 15 分を超える／分割並列が要る場合は `statemachine/{name}.asl.json` と `AWS::Serverless::StateMachine`（雛形に任意で同梱）を使い、Scheduler からは判定用の `trigger` Lambda を呼んで `states:StartExecution` させる。
6. `pytest --cov=functions --cov-fail-under=80` と `sam validate --lint`（cfn-lint 1.51.3 以上）を通す。
7. **テストはアプリ単位で実行する**（`uv run pytest apps/{app}/tests`）。`functions/api` / `functions/batch` というモジュール名は全アプリで同じため、複数アプリのテストを 1 つの pytest セッションに混ぜると sys.path 上で別アプリのモジュールが黙って誤解決し得る。2 アプリ目の骨格作成時に per-app conftest での sys.path 分離（またはパッケージ名前空間化）を判断する。

### ステップ 5: dev にデプロイして直接疎通する

```bash
cd apps/{app}
sam build --use-container && sam deploy
```

- デプロイで `/example-backoffice/dev/apps/{app}/api-domain` が SSM に書かれる。
- `execute-api` に直接アクセスして確認する（ID トークンと `x-origin-verify` が必要）。

```bash
TOKEN=$(scripts/get-id-token.sh dev {E2E ユーザー})
SECRET=$(aws ssm get-parameter --name /example-backoffice/dev/edge/origin-verify-secret --with-decryption --query Parameter.Value --output text)
API=$(aws ssm get-parameter --name /example-backoffice/dev/apps/{app}/api-domain --query Parameter.Value --output text)
curl -s https://$API/api/{app}/health -H "Authorization: Bearer $TOKEN" -H "x-origin-verify: $SECRET"
```

### ステップ 6: edge に origin と behavior を追加する

- 雛形が出力した `apps/{app}/edge-snippet.yaml`（パラメータ 1 + origin 1 + behavior 1）を `infra/edge/template.yaml` の該当箇所に転記する。
- **behavior には `FunctionAssociations`（viewer-request → `EdgeGuardFunction`）を必ず入れる。** 付け忘れるとそのアプリの API だけ社内 IP 制限を素通りする（DESIGN.md 7.1）。
- **`scripts/deploy-edge.sh` に新アプリ分を追記する。** api-domain は samconfig ではなくこのスクリプトが SSM から読んで `parameter_overrides` で渡す方式のため、スクリプト内の saas-a-billing のブロック（`API_DOMAIN_PARAM` の取得 → `OVERRIDES` への `{App}ApiDomain=...` 連結。未登録なら渡さない）を新アプリ用に 1 組複製する。
- `scripts/deploy-edge.sh dev` でデプロイする。
- `https://backoffice-dev.example-cloud.com/api/{app}/health` を ID トークン付きで呼んで 200 を確認する。

```yaml
Parameters:
  SaasABillingApiDomain:
    Type: String               # SSM 型ではなく素の String。deploy-edge.sh が SSM から読んで渡す
    Default: ""                # 空なら API origin / behavior を作らない（app 未デプロイでも edge は立つ）
Conditions:
  HasSaasABillingApi: !Not [!Equals [!Ref SaasABillingApiDomain, ""]]
Resources:
  Distribution:
    Properties:
      DistributionConfig:
        Origins:
          - Id: api-saas-a-billing    # HasSaasABillingApi 条件付き（不成立時は AWS::NoValue）
            DomainName: !Ref SaasABillingApiDomain
            CustomOriginConfig: { OriginProtocolPolicy: https-only, OriginSSLProtocols: [TLSv1.2] }
            OriginCustomHeaders:
              - { HeaderName: x-origin-verify, HeaderValue: !Ref OriginVerifySecret }
        CacheBehaviors:
          - PathPattern: /api/saas-a-billing/*
            TargetOriginId: api-saas-a-billing
            ViewerProtocolPolicy: https-only
            AllowedMethods: [GET, HEAD, OPTIONS, PUT, PATCH, POST, DELETE]
            CachePolicyId: 4135ea2d-6df8-44a3-9df3-4b5a84be39ad
            OriginRequestPolicyId: b689b0a8-53d0-40ab-baf2-68738e2966ac
            FunctionAssociations:
              - EventType: viewer-request
                FunctionARN: !GetAtt EdgeGuardFunction.FunctionARN
            Compress: true
```

### ステップ 7: フロントにマニフェストを追加する

1. `frontend/src/apps/_template/` を `frontend/src/apps/{app}/` にコピーする。
2. `index.ts` の `id` / `title` / `icon` / `order` / `requiredGroups` / `nav` を書き換える。設定画面の nav は `requiredGroups: ['admin']` にする。
3. `routes.ts` に画面を追加する。`path` は相対、`component` は `() => import('./views/Xxx.vue')`。
4. `api.ts` で `createAppApi('{app}')` を作り、エンドポイント関数をまとめる。
5. ストアは `defineStore('{app}/{name}', ...)`。他アプリのストアを参照しない。
6. `__tests__/` にユニットテストを書き、`npm test`（`registry.test.ts` を含む）を通す。
7. `npm run build`（`vue-tsc -b && vite build`）を通す。
8. `scripts/deploy-frontend.sh dev` で配布する。サイドバー・ルーター・権限は自動反映され、他ファイルの修正は不要。

### ステップ 8: E2E を書いて dev で通す

- `frontend/e2e/{app}.spec.ts` に主要導線を 1 本書く（ログイン → サイドバー → 一覧表示 → 主要操作）。
- `E2E_BASE_URL=https://backoffice-dev.example-cloud.com npx playwright test e2e/{app}.spec.ts`。
- 通知を使うアプリは、dev の Slack チャンネル（既定 `#proj-saas-a-billing`。チャンネル ID で指定する）とテスト用メール宛先で受信を確認する。

### ステップ 9: レビューを受ける

- PR に 4 章のチェックリストを転記し、すべてに印を付ける。
- PR の差分は `apps/{app}/`、`frontend/src/apps/{app}/`、`infra/edge/template.yaml`（+ samconfig）、`Documents/apps/{app}/`、`README.md`（アプリ一覧）に収まる。他の場所に差分がある場合は理由を PR に書く。

### ステップ 10: prod にリリースする

1. `cd apps/{app} && sam build --use-container && sam deploy --config-env production`
2. `scripts/deploy-edge.sh prod`
3. `scripts/deploy-frontend.sh prod`
4. `https://backoffice.example-cloud.com/{app}` でログイン → 一覧表示 → 主要操作を確認する。
5. 利用者へ周知する（Slack）。アプリの設定画面に初期値の入力が必要な場合は `admin` グループの担当者に依頼する。

---

## 3. 雛形テンプレート（`templates/app/`）

`sam init --location ./templates/app` で使う cookiecutter 形式。生成物は次のとおり。

| パス | 内容 |
| --- | --- |
| `apps/{app}/template.yaml` | HTTP API（`$default` ステージ、JWT authorizer、`ANY /api/{app}/{proxy+}`）、api Lambda（shared / Powertools / 社内レイヤ）、DynamoDB 単一テーブル、ログ グループ（365 日）、SSM `/apps/{app}/api-domain`、`ScheduleV2` バッチ（コメントアウト）、`AWS::Serverless::StateMachine`（コメントアウト）、IAM（SQS SendMessage、S3 `{app}/*`、SSM `apps/{app}/*`、DynamoDB 自テーブル、`states:StartExecution`） |
| `apps/{app}/samconfig.toml` | `[default]`（dev）と `[production]`。production は SSM キー名を上書き済み。`tags` に `Project` / `App` / `Env` |
| `apps/{app}/functions/api/app.py` | `APIGatewayHttpResolver(strip_prefixes=[...])`、`/health`、`origin_verify`、`LambdaLogger.contextualize` |
| `apps/{app}/functions/requirements.txt` | 空（依存はレイヤ）。必要なライブラリを追記。`functions/` 全体で 1 つの `CodeUri` |
| `apps/{app}/functions/batch/handlers/scheduled.py` | Scheduler から呼ばれる雛形（`{"trigger": "scheduled" | "manual"}`） |
| `apps/{app}/statemachine/run.asl.json` | Step Functions を使う場合のステートマシン定義（任意。`DefinitionSubstitutions` で関数 ARN を注入） |
| `apps/{app}/tests/` | `conftest.py`（moto、JWT クレーム付きイベントのファクトリ）、`test_health.py` |
| `apps/{app}/edge-snippet.yaml` | edge に転記する origin / behavior / パラメータ |
| `apps/{app}/README.md` | アプリのローカル実行・デプロイ手順 |
| `frontend/src/apps/_template/` | `index.ts`（マニフェスト）、`routes.ts`、`api.ts`、`views/IndexView.vue`、`__tests__/` |

- 雛形の改訂を既存アプリへ配る必要が出た時点で copier 化を検討する（現時点は cookiecutter 形式）。

---

## 4. レビューチェックリスト

PR の説明に転記して使う。

### IAM 最小権限

- [ ] Lambda ロールは関数ごとに作られている（共有ロールなし）
- [ ] `Resource: "*"` は Resource を限定できないアクションだけに使われている
- [ ] S3 は `{bucket}/{app}/*` に限定、SQS は `/notify/queue-arn` に限定、SSM は `/example-backoffice/{env}/apps/{app}/*` と `/edge/origin-verify-secret` に限定
- [ ] ステートマシンを使う場合、`states:StartExecution` が自アプリのステートマシン ARN に限定されている（ステートマシンロールの `lambda:InvokeFunction` も呼び出す関数の ARN に限定）
- [ ] DynamoDB は自アプリのテーブル ARN に限定

### SSM 契約

- [ ] `infra/edge/template.yaml` に追加した behavior に `FunctionAssociations`（viewer-request → `EdgeGuardFunction`）が入っている（DESIGN.md 7.1）
- [ ] `template.yaml` の `AWS::SSM::Parameter::Value<String>` の `Default` が DESIGN.md 4.2 のキー名と一致している
- [ ] `samconfig.toml` の `[production]` が prod のキー名を上書きしている（dev のキーが残っていない）
- [ ] `scripts/deploy-edge.sh` に新アプリの api-domain 取得と `parameter_overrides` 連結を追記した（ステップ 6）
- [ ] ID / ARN / URL の値を samconfig や `.env` にコピーしていない
- [ ] `/apps/{app}/api-domain` を書く `AWS::SSM::Parameter` がある

### 認証・認可

- [ ] HTTP API に `DefaultAuthorizer`（JWT）が設定されている
- [ ] 全 API Lambda に `origin_verify` ミドルウェアが付いている
- [ ] 設定変更 API が `require_group("admin")` で保護されている
- [ ] フロントの設定画面の nav / route に `requiredGroups: ['admin']` がある

### タグ・命名

- [ ] samconfig の `tags` に `Project=example-backoffice App={app} Env={env}` がある
- [ ] 各リソースに `Name` タグ（`Function` / `Api` / `Table` / `LogGroup` / `Role` / `StateMachine` / `Schedule` など）がある
- [ ] スタック名・関数名・テーブル名が DESIGN.md 14 章の規約に従っている

### ログ・運用

- [ ] `AWS::Logs::LogGroup` を明示し、保持 365 日
- [ ] API アクセスログが有効
- [ ] `LambdaLogger` を使い、`@LambdaLogger.contextualize` が付いている
- [ ] 秘密情報（トークン・パスワード・Webhook URL）をログに出していない
- [ ] バッチがある場合、Scheduler の DLQ と Lambda の `OnFailure` 送信先がある
- [ ] ステートマシンを使う場合、`statemachine/*.asl.json` がリポジトリにあり、`ExecutionsFailed` / `ExecutionsTimedOut` のアラームがある
- [ ] 30 秒を超える処理を API から同期実行していない

### フロント

- [ ] `index.ts` のマニフェストが `defineApp()` で定義され、`registry.test.ts` が通る
- [ ] `routes.ts` の `component` がすべて `() => import()`（遅延ロード）
- [ ] 他アプリのストア・コンポーネントを直接 import していない
- [ ] 日本語フォントを明示している（`styles/settings.scss` を上書きしていない。独自 CSS で SimSun / SimHei / Microsoft YaHei / PingFang SC / Noto Sans SC を指定していない）
- [ ] Excel 等の生成物にも日本語フォント（Meiryo 等）を明示している

### テスト・文書

- [ ] バックエンドのカバレッジ 80% 以上、`sam validate --lint`（cfn-lint 1.51.3 以上）が通る
- [ ] Vitest が通り、`npm run build` が通る
- [ ] E2E を 1 本以上 dev で通した
- [ ] `Documents/apps/{app}/DESIGN.md` がある
- [ ] `README.md` のアプリ一覧に追記した

---

## 5. よくある失敗

| 症状 | 原因 | 対処 |
| --- | --- | --- |
| `sam deploy` が `Parameter ... cannot be found` で失敗 | SSM キーが存在しない（shared / core 未デプロイ、または env の綴り違い） | `get-parameters-by-path` でキーを確認。デプロイ順（shared → core → app → edge）を守る |
| prod のアプリが dev の Cognito を見ている | `samconfig.toml` の `[production]` で SSM キー名を上書きし忘れた | `[production.deploy.parameters]` に prod のキー名をすべて書く |
| 全ルートが 404 | `strip_prefixes` の値が `/api/{app}` と一致していない | ルート `/api/{app}/{proxy+}` と `strip_prefixes` を揃える |
| CloudFront 経由で 403（API Gateway のメッセージ） | edge の origin request policy に `AllViewer`（Host 込み）を使った | `AllViewerExceptHostHeader` に戻す |
| CloudFront 経由で POST だけ失敗 | behavior の `AllowedMethods` を省略した（GET / HEAD のみ） | 7 メソッドを明示する |
| `execute-api` 直叩きで 403 | `x-origin-verify` ヘッダが無い、または値が古い | SSM から現在の値を取得して付ける。ローカルは `ORIGIN_VERIFY_SKIP=1` |
| SPA のディープリンクで 403 / 空白 | CloudFront Function（`EdgeGuardFunction`）が default behavior に付いていない、または `CustomErrorResponses` を足した | 5.4 / 7.1 の関数を**全 behavior**（default と `/api/{app}/*`）に付ける。`CustomErrorResponses` は使わない |
| 社外 IP から自分のアプリの API だけ 403 にならない | 追加した behavior に `FunctionAssociations`（viewer-request）を付け忘れた | 7.1 のとおり `EdgeGuardFunction` を付けて `scripts/deploy-edge.sh {env}` |
| API の 401 が SPA の HTML（200）で返る | `CustomErrorResponses` を設定した | 削除する |
| `admin` なのに 403 | `cognito:groups` が文字列 `"[admin]"` で来ている / 自前でクレームを解析した | shared `auth.get_claims()` を使う |
| shared を直したのにアプリの挙動が変わらない | レイヤの新バージョンはアプリの再デプロイで取り込まれる | `apps/{app}` を `sam deploy` する |
| Excel 生成 API が CloudFront で 504 | 30 秒を超えた | 非同期化（Lambda 非同期 Invoke / Step Functions）してポーリングにする |
| メール添付のファイル名が文字化け | 非 ASCII ファイル名 | ASCII 名（例 `saas_a_billing_2026-08.xlsx`）にする |
| notifier が DLQ に落ちる（`NoSuchKey`） | 添付を `{app}/` 以外のプレフィックスに置いた、または送信前に削除した | `notify.put_attachment()`（キー規約 `{app}/[{subprefix}/]{yyyy}/{mm}/{filename}`）を使う。添付は送信完了まで残す |
| `AccessDenied`（S3 PutObject） | IAM の Resource が `{app}/*` と一致していない | プレフィックスとアプリ id を揃える |
| サイドバーにアプリが出ない | `index.ts` が `default export` でない、フォルダ名が `_` で始まる、`requiredGroups` に所属していない | `registry.test.ts` を実行。グループ所属を確認 |
| 子ルートが親に繋がらない | `routes.ts` の `path` を `/` で始めた | 相対パス（`''`、`settings`）にする |
| 初回表示が遅い | マニフェストからビューを静的 import した | `component: () => import(...)` にする |
| 漢字が中華フォントで表示される | `index.html` の `lang` が `ja` でない、独自 CSS で font-family を上書きした | `lang="ja"`、`styles/settings.scss` の日本語フォントを使う |
| ログの保持期間が「無期限」 | `AWS::Logs::LogGroup` を定義せず Lambda に自動作成させた | テンプレートで LogGroup を明示（365 日） |
| コスト配分レポートにアプリが出ない | `Project` / `Name` タグ漏れ | samconfig の `tags` と各リソースの `Name` を付ける |
| E2E がログインで止まる | dev の E2E ユーザーに TOTP を登録した | E2E ユーザーは TOTP 未登録のまま使う（dev が `ON` の場合は `otplib` で入力） |
