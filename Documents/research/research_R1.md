# example-backoffice 基盤構成 調査レポート R1

- 調査日: 2026-08-27
- 対象: 社内向け保守管理サイト `example-backoffice`（CS部・開発部、第一弾 = SaaS_A 月額課金レポート）
- 前提技術: AWS SAM / Python 3.13 Lambda / DynamoDB / Vue3 SPA（CloudFront + S3）/ Cognito / SES / Slack
- 注記: AWS Pricing MCP は本エージェントから利用できなかったため、料金は AWS 公式サイト（aws.amazon.com の料金ページ、および同サイトが参照する料金 JSON エンドポイント b0.p.awsstatic.com）から取得した。単価の単位は各表に明記する。

---

## 0. 結論サマリ

| 論点 | 結論 |
| --- | --- |
| 案1（CloudFront 単一入口 + `/api/<app>/*` → アプリ別 HTTP API）の妥当性 | **妥当。AWS 公式サンプル（aws-samples/integrate-httpapi-with-cloudfront-and-waf 等）と同型**。同一オリジンで CORS 不要、WAF を 1 箇所で適用できる |
| スタック間の契約 | **SSM Parameter Store を「契約レジストリ」にする**（`/example-backoffice/{env}/...`）。アプリ側テンプレートは `Type: AWS::SSM::Parameter::Value<String>` で受け取り、開発者は値をコピーしない。CloudFormation Export は infra 内部の強参照だけに限定。新しい `Fn::GetStackOutput` は候補だがツール対応が未確認 |
| `/api/<app>` プレフィックスの剥がし方 | **API 側で受ける**（HTTP API ルートを `/api/<app>/{proxy+}` にし、Lambda では Powertools `strip_prefixes`）。CloudFront Function は書かない |
| 仮説からの変更点 1 | **`CustomErrorResponses`（403/404 → index.html）は使わない**。ディストリビューション全体に効くため API の 401/403 が index.html(200) に化ける。SPA フォールバックは CloudFront Function で行う |
| 仮説からの変更点 2 | **infra を `core`（Cognito/SES/SSM/通知）と `edge`（CloudFront/WAF/S3）に分割**。アプリ追加時に触るのは edge の origin+behavior 10 行だけ |
| 仮説からの変更点 3 | HTTP API はリソースポリシー・WAF 非対応（公式比較表）。**execute-api 直叩きで IP 制限を迂回できる**ため、CloudFront の origin カスタムヘッダ `x-origin-verify` を共通レイヤで検証する |
| 通知 | **(a) 共通 SQS キュー + 共通 notifier Lambda** を採用し、(b) の薄いクライアント（メッセージ組立と送信だけ）を共通レイヤで配る。添付は S3 参照で渡し、notifier が SES v2 `SendEmail` の `Attachments` で送る（40MB 上限） |
| WAF | IP allowlist は WAF で 1 箇所（SPA も API も同一ディストリビューション）。**固定費 約 $6/月**。CloudFront 定額 Free プラン（$0、WAF 込み・ルール 5 本）が使えれば $0 になる可能性あり（要検証）。ref-project-a 流の CloudFront Function 判定（$0）も選択肢 |
| 月額概算 | **約 $8/月（うち WAF $6）**。少人数・月次バッチ中心なら他はほぼ無料枠内 |

---

## 1. 構成の妥当性と推奨構成

### 1.1 全体像

```
利用者(社内IP) ──HTTPS──> CloudFront (WAF: IP allowlist)
                             ├─ /            → S3 (SPA, OAC)            + CloudFront Function: SPA フォールバック
                             ├─ /api/billing/* → HTTP API (billing)      ─ JWT authorizer(Cognito) → Lambda
                             ├─ /api/<app2>/*  → HTTP API (<app2>)       ─ JWT authorizer(Cognito) → Lambda
                             └─ ...
Cognito User Pool（@example.co.jp 限定セルフサインアップ / 将来 MFA ON）
共通通知: apps → SQS(notify-queue) → notifier Lambda → SES(添付は S3 参照) / Slack Webhook
```

- CloudFront → API Gateway の公式手順（re:Post「Set up API Gateway with a custom CloudFront distribution」）および aws-samples の Terraform 実装と同じ形。
- HTTP API は `$default` ステージを使うとベース URL 直下で呼べる（ステージパス無し）ため、CloudFront の `OriginPath` は不要。SAM の `AWS::Serverless::HttpApi` は `StageName` 省略時に `$default` を使う。

### 1.2 スタック分割と「契約」の渡し方

#### スタック構成（推奨）

| スタック | 内容 | ライフサイクル | 所有 |
| --- | --- | --- | --- |
| `example-backoffice-core-{env}` | Cognito User Pool/Client + PreSignUp Lambda、SES 送信 ID、通知用 SQS+DLQ、添付用 S3、notifier Lambda、SSM 契約パラメータ | 変更頻度低 | 基盤担当 |
| `example-backoffice-edge-{env}` | S3(SPA) + OAC、CloudFront、WAF WebACL(us-east-1)、Route53 | アプリ追加時に origin/behavior 追加 | 基盤担当（アプリ追加 PR はアプリ担当） |
| `example-backoffice-shared-{env}` | 共通 Lambda Layer（認可ヘルパ、通知クライアント、origin-verify ミドルウェア） | 共通コード変更時 | 基盤担当 |
| `example-backoffice-app-<app>-{env}` | HTTP API + JWT authorizer、Lambda、DynamoDB、EventBridge Scheduler（バッチ） | アプリごと | アプリ担当 |

- 「ライフサイクルと所有者で分ける」は CloudFormation 公式ベストプラクティスそのもの。
- edge を core から分けるのは、CloudFront（更新に数分・失敗時の影響大）とアプリ追加以外で触らない Cognito を同じ変更セットに乗せないため。
- ref-project-a のネストスタック方式（親 1 本）は「1 チーム 1 アプリ」なら良いが、複数人が別アプリを足す前提では **独立スタック（example-portal 方式）** の方が競合しない。

#### 契約の渡し方 比較

| 方式 | 長所 | 短所 | 用途 |
| --- | --- | --- | --- |
| CloudFormation Export / `Fn::ImportValue` | 強参照（参照中の Export 側は削除・値変更不可 = 安全）、追加 IAM 不要 | Export 名はリージョン内一意・変更不可。参照されている値は infra 側で変えられない（例: User Pool 作り直し）。名前の typo をデプロイまで検出できない | infra 内部（core→edge）の強参照のみ |
| **SSM Parameter Store + `AWS::SSM::Parameter::Value<String>` 型パラメータ** | テンプレートの `Default` にキー名を書くだけで値の受け渡し不要。infra 側で値を差し替えても参照側は次回デプロイで追従。Lambda 実行時にも同じキーで読める（Powertools Parameters）。`aws ssm get-parameters-by-path` で契約一覧を確認できる | 弱参照（infra を消しても止まらない。デプロイ時点の値で固定）。SecureString はテンプレートパラメータ型として使えない（Secrets は Lambda 実行時に取得） | **アプリ ⇔ infra の契約（推奨）** |
| samconfig `parameter_overrides` | 単純 | ID を人がコピーする＝ミスの温床。環境ごとに二重管理 | 環境固有の非共有値（LogLevel、アプリ固有設定）だけ |
| `Fn::GetStackOutput`（新機能） | Export 不要で他スタックの Output を参照。クロスアカウント/リージョン可 | 弱参照。`Outputs` 直下や `Fn::Sub` 内で使えない等の制限あり。SAM CLI / cfn-lint の対応状況が未確認 | 将来の選択肢。現時点では採用見送り |

#### 契約レジストリ（SSM キー設計）

```
/example-backoffice/{env}/cognito/user-pool-id
/example-backoffice/{env}/cognito/user-pool-arn
/example-backoffice/{env}/cognito/client-id
/example-backoffice/{env}/cognito/issuer-url          # https://cognito-idp.ap-northeast-1.amazonaws.com/<poolId>
/example-backoffice/{env}/notify/queue-url
/example-backoffice/{env}/notify/queue-arn
/example-backoffice/{env}/notify/attachments-bucket
/example-backoffice/{env}/shared/layer-arn             # shared スタックが書く
/example-backoffice/{env}/edge/origin-verify-secret    # SecureString（Lambda が実行時に読む）
/example-backoffice/{env}/apps/<app>/api-domain        # 各 app スタックが書く（edge が読む）
```

- core/shared/app の各スタックは `AWS::SSM::Parameter` リソースで自分の Output を書く（Export の代わり）。
- app テンプレート側の受け口は次の形。開発者は `Default` のキー名を雛形から変えない。

```yaml
Parameters:
  Env: { Type: String, AllowedValues: [dev, prod], Default: dev }
  UserPoolIssuer:
    Type: AWS::SSM::Parameter::Value<String>
    Default: /example-backoffice/dev/cognito/issuer-url
  UserPoolClientId:
    Type: AWS::SSM::Parameter::Value<String>
    Default: /example-backoffice/dev/cognito/client-id
  NotifyQueueUrl:
    Type: AWS::SSM::Parameter::Value<String>
    Default: /example-backoffice/dev/notify/queue-url
  SharedLayerArn:
    Type: AWS::SSM::Parameter::Value<String>
    Default: /example-backoffice/dev/shared/layer-arn
```

- `Default` に env を埋め込めないので、prod は samconfig の `[prod.deploy.parameters]` で `UserPoolIssuer=/example-backoffice/prod/cognito/issuer-url` のようにキー名（値ではない）を上書きする。雛形が両環境分を生成する。

### 1.3 命名規約

| 対象 | 規約 | 例 |
| --- | --- | --- |
| スタック名 | `example-backoffice-{layer|app-<app>}-{env}` | `example-backoffice-app-billing-dev` |
| SSM キー | `/example-backoffice/{env}/{domain}/{key}`（小文字ケバブ） | 上記 |
| Export 名（infra 内部のみ） | `example-backoffice-{env}-{layer}-{resource}` | `example-backoffice-dev-core-user-pool-arn` |
| Lambda 関数名 | `example-backoffice-{app}-{role}-{env}` | `example-backoffice-billing-api-dev` |
| DynamoDB | `example-backoffice-{app}-{table}-{env}` | `example-backoffice-billing-settings-dev` |
| API パス | `/api/<app>/...`（`<app>` は小文字英数とハイフン、SPA のルート `/<app>/...` と一致させる） | `/api/billing/reports` |
| S3 添付プレフィックス | `s3://<attachments-bucket>/<app>/<yyyy>/<mm>/<file>` | |
| タグ | `Project=example-backoffice`, `App=<app>`, `Env=<env>`, `Name=<役割>`（社内規約の Project+Name に App を追加） | |
| CloudWatch Logs | `/aws/lambda/<関数名>`、保持 365 日（ref-project-a に合わせる） | |

### 1.4 アプリ雛形とチェックリスト運用

| 手段 | 評価 |
| --- | --- |
| `sam init --location <path|git>` | ローカルは cookiecutter 形式か zip のみ対応。`--no-input` / `--extra-content` で非対話生成可。**社内標準 CLI（SAM）だけで完結する**のが利点 |
| cookiecutter 単体 | `sam init --location` と同じテンプレートが使える。生成後の更新追従は無い（cruft で補える） |
| **copier** | 生成後に `copier update` でテンプレート改訂を既存アプリへ再適用できる（answers file と migrations を持つ）。「共通部分を後から直したい」monorepo 向き |

- 推奨: **cookiecutter 形式で作り、`sam init --location ./templates/app` で生成**（依存ツール追加なし）。テンプレート改訂を既存アプリに配る必要が出たら copier 化を検討。
- 雛形の生成物: `template.yaml`（HTTP API + JWT + strip_prefixes 済み Lambda + DynamoDB + Scheduler 雛形）、`samconfig.toml`（dev/prod）、`src/app.py`（Powertools `APIGatewayHttpResolver`）、`tests/`、`README.md`、`frontend/src/apps/<app>/`（Vue ルートとメニュー登録の断片）、`edge` 用の origin/behavior スニペット。
- チェックリストは `docs/ADD_APP_CHECKLIST.md` に置き、PR テンプレート（`.github/PULL_REQUEST_TEMPLATE/add-app.md`）にチェックボックスとして転記する。

### 1.5 推奨ディレクトリツリー

```
example-backoffice/
├── README.md                      # 全体像・デプロイ順・アプリ追加手順へのリンク
├── docs/
│   ├── ARCHITECTURE.md
│   ├── CONTRACT.md                # SSM キー一覧（契約レジストリの正本）
│   └── ADD_APP_CHECKLIST.md
├── infra/
│   ├── core/                      # Cognito / SES / SQS+DLQ / 添付S3 / notifier Lambda / SSM契約
│   │   ├── template.yaml
│   │   ├── samconfig.toml
│   │   └── src/{pre_signup,notifier}/
│   └── edge/                      # S3(SPA)+OAC / CloudFront / WAF(us-east-1) / Route53 / CFF
│       ├── template.yaml          # アプリ追加時に origin+behavior を追記
│       ├── waf-template.yaml      # us-east-1 に別スタック（WebACL は CLOUDFRONT スコープ）
│       ├── functions/spa-fallback.js
│       └── samconfig.toml
├── shared/                        # 共通 Lambda Layer
│   ├── template.yaml
│   └── layers/example_backoffice/
│       ├── auth.py                # JWT クレーム取り出し・ロール判定
│       ├── origin_verify.py       # x-origin-verify 検証ミドルウェア
│       └── notify.py              # send_notification(...) 薄いクライアント
├── apps/
│   └── billing/                   # 独立 SAM スタック
│       ├── template.yaml
│       ├── samconfig.toml
│       ├── src/{api,batch}/
│       └── tests/
├── frontend/                      # 単一 Vue3 SPA
│   ├── src/apps/billing/          # アプリ別ルート・画面
│   ├── src/apps/registry.ts       # アプリ登録（メニュー・ルート）
│   └── src/shared/{auth,api}/     # Cognito(Amplify Auth) と fetch ラッパ
├── templates/
│   └── app/                       # sam init --location 用 cookiecutter テンプレート
│       ├── cookiecutter.json
│       └── {{cookiecutter.app_name}}/...
└── scripts/
    ├── deploy-all.sh              # 初回のみ: core → shared → apps → edge → frontend
    └── deploy-frontend.sh         # build → s3 sync → invalidation
```

---

## 2. CloudFront → 複数 HTTP API のパスベースルーティング 実装上の注意

### 2.1 origin とステージ

- origin ドメイン: `${ApiId}.execute-api.ap-northeast-1.amazonaws.com`（`OriginProtocolPolicy: https-only`）。
- HTTP API は `$default` ステージ（SAM の既定）を使い、`OriginPath` は空にする。名前付きステージにすると `OriginPath: /dev` が必要になり、環境差分が edge に漏れる。
- CloudFront の origin 応答タイムアウト既定 30 秒、HTTP API の統合タイムアウト上限 30 秒。重い処理（Excel 生成）は API から同期実行せず、SQS/Step Functions/EventBridge Scheduler で非同期化し、SPA はポーリングまたは通知で受ける。

### 2.2 Authorization ヘッダ転送とキャッシュ無効化

- CloudFront は既定で `Authorization` を origin に渡さない。`/api/*` behavior には次の 2 つのマネージドポリシーを組み合わせる。
  - Cache policy: `CachingDisabled` = `4135ea2d-6df8-44a3-9df3-4b5a84be39ad`
  - Origin request policy: `AllViewerExceptHostHeader` = `b689b0a8-53d0-40ab-baf2-68738e2966ac`（公式に「API Gateway / Lambda URL 向け」と明記。`Host` を除いて全ヘッダ・Cookie・クエリを転送し、CloudFront が origin ドメインの `Host` を付け直す）
- `AllViewer`（Host 込み）を使うと API Gateway が 403 を返す（Host 不一致）。
- `AllowedMethods` は `[GET, HEAD, OPTIONS, PUT, PATCH, POST, DELETE]` を明示する（既定は GET/HEAD のみで POST が 403 になる）。
- `ViewerProtocolPolicy: https-only`、`Compress: true`。

### 2.3 `/api/<app>` プレフィックスの扱い（API 側で受ける = 推奨）

| 方式 | 内容 | 評価 |
| --- | --- | --- |
| **API 側で受ける** | HTTP API のルートを `ANY /api/billing/{proxy+}` にし、Lambda は Powertools `APIGatewayHttpResolver(strip_prefixes=["/api/billing"])` で `/reports` として扱う | CloudFront Function 不要。execute-api 直叩きでも同じパスなのでローカル/直接テストが CloudFront 経由と一致。**推奨** |
| CloudFront Function で剥がす | viewer-request で `request.uri = request.uri.replace(/^\/api\/[^/]+/, '')` | 関数 1 本で全 behavior に流用可（`FunctionAssociations` で各 behavior に付与）。ただし直叩き時とパスが変わり、混乱の元 |

- Powertools の `strip_prefixes` は文字列の他に正規表現も受け付ける（環境やバージョン違いの吸収に使える）。剥がした後のルートパスは `/` にマップされる。

### 2.4 `CustomErrorResponses` の罠（仮説からの変更点）

- ref-project-a と同じ「403/404 → `/index.html`(200)」設定は **ディストリビューション全体に効き、`/api/*` behavior の応答にも適用される**。JWT authorizer が返す 401/403 が index.html の 200 に化け、SPA 側でエラー処理できなくなる（aws-samples リポジトリの Issue #186 で同じ事故が報告されている）。
- 対策: `CustomErrorResponses` を使わず、**S3 の既定 behavior だけに CloudFront Function（viewer-request）を付けて拡張子の無いパスを `/index.html` に書き換える**。

```javascript
// infra/edge/functions/spa-fallback.js (cloudfront-js-2.0)
function handler(event) {
  var req = event.request;
  var uri = req.uri;
  if (!uri.startsWith('/api/') && !uri.includes('.')) {
    req.uri = '/index.html';
  }
  return req;
}
```

- S3 は OAC 経由でオブジェクト未存在時に 403 を返すが、上の書換で index.html に到達するため 403 は発生しない。

### 2.5 origin 直叩き（execute-api）対策

- 公式比較表のとおり HTTP API は **リソースポリシー非対応・WAF 非対応**。`DisableExecuteApiEndpoint` はカスタムドメイン併用時のみ意味があり、そのカスタムドメインも公開されるため解決にならない。
- したがって WAF の IP allowlist は CloudFront 経由のトラフィックにしか効かない。JWT は必須なので「認証済み社員が社外から API を直叩きできる」だけだが、IP 制限の趣旨（社外からの利用禁止）を保つには次を推奨。
  1. edge の origin に `OriginCustomHeaders: x-origin-verify: <secret>` を付ける（値は SSM SecureString/Secrets Manager から `{{resolve:...}}` 動的参照）。
  2. 共通レイヤの `origin_verify.py`（Powertools ミドルウェア）で `event.headers["x-origin-verify"]` を検証し、不一致は 403。値は実行時に SSM から取得しキャッシュ。
  3. 公式パターン（AWS ブログ / serverless-patterns `apigw-http-api-waf-cloudfront-terraform`）は Lambda authorizer + Secrets Manager 自動ローテーションだが、JWT authorizer と Lambda authorizer は 1 ルートに併用できないため、本件では JWT authorizer を残して Lambda 内検証にする。
- Cognito のトークン発行エンドポイントも社外から到達できる。厳密に閉じたい場合は Cognito User Pool にリージョナル WebACL（別途 $5+$1/月）を関連付ける。MFA 移行後は不要。

### 2.6 SAM 定義例（app 側）

```yaml
Resources:
  HttpApi:
    Type: AWS::Serverless::HttpApi
    Properties:
      Auth:
        DefaultAuthorizer: CognitoJwt
        Authorizers:
          CognitoJwt:
            IdentitySource: $request.header.Authorization
            JwtConfiguration:
              issuer: !Ref UserPoolIssuer
              audience: [!Ref UserPoolClientId]
      AccessLogSettings:
        DestinationArn: !GetAtt ApiAccessLogs.Arn
        Format: '{"requestId":"$context.requestId","path":"$context.path","status":"$context.status","sub":"$context.authorizer.claims.sub"}'
  ApiFunction:
    Type: AWS::Serverless::Function
    Properties:
      Handler: app.handler
      Layers: [!Ref SharedLayerArn]
      Environment:
        Variables:
          API_PREFIX: /api/billing
      Events:
        Api:
          Type: HttpApi
          Properties:
            ApiId: !Ref HttpApi
            Path: /api/billing/{proxy+}
            Method: ANY
            PayloadFormatVersion: "2.0"
  ApiDomainParam:
    Type: AWS::SSM::Parameter
    Properties:
      Name: !Sub /example-backoffice/${Env}/apps/billing/api-domain
      Type: String
      Value: !Sub ${HttpApi}.execute-api.${AWS::Region}.amazonaws.com
```

- JWT authorizer は Cognito のアクセストークン（`client_id` クレーム）と ID トークン（`aud`）の両方を受け付ける（`aud` が無い場合に `client_id` を検証）。SPA からは **アクセストークン**を `Authorization: Bearer` で送る。

### 2.7 edge 側の追記単位（アプリ 1 つ = origin 1 + behavior 1 + パラメータ 1）

```yaml
Parameters:
  BillingApiDomain:
    Type: AWS::SSM::Parameter::Value<String>
    Default: /example-backoffice/dev/apps/billing/api-domain
Resources:
  Distribution:
    Properties:
      DistributionConfig:
        Origins:
          - Id: api-billing
            DomainName: !Ref BillingApiDomain
            CustomOriginConfig: { OriginProtocolPolicy: https-only, OriginSSLProtocols: [TLSv1.2] }
            OriginCustomHeaders:
              - HeaderName: x-origin-verify
                HeaderValue: '{{resolve:secretsmanager:example-backoffice/dev/origin-verify:SecretString:token}}'
        CacheBehaviors:
          - PathPattern: /api/billing/*
            TargetOriginId: api-billing
            ViewerProtocolPolicy: https-only
            AllowedMethods: [GET, HEAD, OPTIONS, PUT, PATCH, POST, DELETE]
            CachePolicyId: 4135ea2d-6df8-44a3-9df3-4b5a84be39ad
            OriginRequestPolicyId: b689b0a8-53d0-40ab-baf2-68738e2966ac
            Compress: true
```

- CloudFront クォータ: cache behavior 75 / origin 100（いずれも引き上げ可）。数十アプリまでは問題ない。
- 代替案（案2）: **共有 HTTP API 1 本**を core に置き、各アプリは `AWS::ApiGatewayV2::Route` + `Integration` を自スタックに追加する方式。edge を一切触らずにアプリを足せるが、SAM の `Events: HttpApi` が使えず `sam local start-api` も効かない。アプリが 10 を超えて edge 更新が負担になった時点で再検討。

---

## 3. WAF による IP allowlist と解除手順

### 3.1 構成

- WebACL は **CLOUDFRONT スコープ = us-east-1 に作成**（公式: グローバル WebACL は N. Virginia 固定）。edge の WAF 部分は us-east-1 用の別テンプレート `infra/edge/waf-template.yaml` にし、WebACL ARN を SSM `/example-backoffice/{env}/edge/web-acl-arn`（us-east-1 側で書く → 東京側は `String` パラメータで受ける。SSM 型パラメータは同一リージョンのみ）で渡す。
- ルール: `DefaultAction: Block`、`IPSetReferenceStatement`（社内 IP/CIDR）にマッチしたら `Allow`。CloudFront は 1 ディストリビューション = 1 WebACL なので **SPA と全アプリ API を 1 箇所で守れる**。守れない経路は 2.5 のとおり（execute-api 直叩き、Cognito エンドポイント）。
- IP 一覧は CloudFormation パラメータ（`CommaDelimitedList`）にし、変更は `sam deploy` で反映。

### 3.2 料金と代替

| 方式 | 月額（東京・小規模） | 備考 |
| --- | --- | --- |
| WAF WebACL + ルール 1 本 | $5.00/WebACL + $1.00/ルール + $0.60/100万リクエスト ≒ **$6.1** | 時間按分。IP セット自体は無料 |
| CloudFront 定額 Free プラン | **$0** | WAF 込み（ルール 5 本まで、IP ベースの制御は Free で可、ヘッダ条件ルールと WAF ログは Pro $15 以上）。許容量 100GB/100万リクエスト。制約: プランは 1 ディストリビューション単位、Free は 3 個/アカウント、「AWS Free Tier 利用中のアカウント」は不可、CloudFront Function/WebACL を他ディストリビューションと共有不可。**要検証**（社内アカウントの適格性と CloudFormation からの指定方法） |
| CloudFront Function で `event.viewer.ip` を判定 | $0（200万呼出/月まで無料） | ref-project-a の実装そのまま。CIDR 判定は自前実装、ブロックのメトリクス無し。IP 一覧を KVS に置けばデプロイ不要で更新可 |

- 推奨: 標準的で監査しやすい **WAF** を第一候補、定額 Free プランが使えるなら WAF 費用を吸収、使えなければ $6/月を許容するか CloudFront Function 方式で $0 にする。

### 3.3 解除手順（社内 IP 制限 → MFA へ切替）

1. core: Cognito `MfaConfiguration: OPTIONAL` → `ON`、`EnabledMfas: [SOFTWARE_TOKEN_MFA]`（User Pool の更新であり作り直しは発生しない）。SPA 側に TOTP 登録フローを追加し、全員が登録した状態にする。
2. edge(WAF): パラメータ `IpAllowlistMode: block|count|off` を用意し、まず `count`（ルールを Count に変更 = 通すが記録）にして 1〜2 週間ログで確認。
3. `off`: `Condition` で IP ルールと WebACL 関連付け（`WebACLId`）を外す。WebACL 自体は Count のまま残しても良いが $5/月かかるので削除。
4. 2.5 の `x-origin-verify` はそのまま残す（IP 制限とは独立に「CloudFront 経由のみ」を保証）。
5. Cognito 側の WebACL を付けていた場合は同時に外す。

---

## 4. 共通通知サービスの設計

### 4.1 比較

| 方式 | 長所 | 短所 |
| --- | --- | --- |
| **(a) 共通 SQS キュー → 共通 notifier Lambda → SES/Slack** | 送信元アプリは `sqs:SendMessage` 1 権限で済む。SES/Slack の資格情報・送信元アドレス・レート制御・再試行/DLQ を 1 箇所に集約。notifier の改修がアプリに波及しない。ref-project-a と同型 | 非同期（送信結果を同期で受け取れない）。メッセージスキーマの版管理が要る |
| (b) 共通レイヤの関数を各アプリが直接呼ぶ | 同期・単純 | 各アプリの IAM に `ses:SendEmail` と Slack Webhook の読取権限が要る。レイヤ更新を全アプリが取り込まないと挙動がばらつく。Lambda のタイムアウト内で送信完了させる必要 |
| (c) 共通 Lambda を `lambda:InvokeFunction` | 実装は単純 | Lambda→Lambda 同期呼出は待ち時間の二重課金と障害連鎖を招く。非同期 Invoke なら (a) と同等だがキュー可視性・DLQ 制御が弱い |

- 推奨: **(a) を基盤とし、(b) の薄いクライアント**（`notify.send(kind, to, subject, body, attachments=[s3 keys], slack_channel)` = メッセージ組立 + `SendMessage` のみ）を shared レイヤで配る。アプリは SES/Slack を知らない。
- Lambda 公式ベストプラクティスの「冪等に書く」「SQS 可視性タイムアウト > 関数タイムアウト」を守る（notifier に Powertools Idempotency、キー = `message_id`）。

### 4.2 添付（Excel 等）の扱い

- SQS のメッセージ上限は 2025-08 に 1 MiB へ拡大したが、Excel は S3 参照で渡す（メッセージにバイナリを載せない）。
- 流れ: アプリが `s3://<attachments-bucket>/<app>/<yyyy>/<mm>/<file>.xlsx` に置く → メッセージに `{"bucket","key","filename"}` を列挙 → notifier が取得し **SES v2 `SendEmail`（Simple content）の `Attachments`** で送る（MIME 組立不要。合計 40MB まで、`.exe` 等の拡張子は不可）。
- バケットは core が作り、アプリごとにプレフィックス制限した `s3:PutObject` を付与。ライフサイクル 30〜90 日で削除。Slack へはファイルを送らず、署名付き URL（短期）または「メール送付済み」の通知だけにする（Slack の添付は Webhook では送れない）。

### 4.3 メッセージスキーマ v1

```json
{
  "schema": "example-backoffice.notify.v1",
  "message_id": "uuid",
  "app": "billing",
  "channels": ["email", "slack"],
  "email": {"to": ["cs@example.co.jp"], "subject": "SaaS_A 月額課金レポート 2026-08", "text": "...", "html": "..."},
  "attachments": [{"bucket": "example-backoffice-attachments-dev", "key": "billing/2026/08/report.xlsx", "filename": "report.xlsx"}],
  "slack": {"channel": "#cs-saasa", "text": "..."}
}
```

- Slack Incoming Webhook URL / SES 送信元は core の SSM SecureString に置き、notifier だけが読む。
- 失敗時は DLQ（maxReceiveCount 3）→ CloudWatch アラーム → Slack（管理者チャンネル）へ。

---

## 5. 先行事例（OSS / aws-samples）

| リポジトリ | 内容 | 参考になる点 |
| --- | --- | --- |
| aws-samples/integrate-httpapi-with-cloudfront-and-waf — https://github.com/aws-samples/integrate-httpapi-with-cloudfront-and-waf | HTTP API + CloudFront + WAF + `x-origin-verify`（Secrets Manager ローテーション、Lambda@Edge で付与、Lambda authorizer で検証） | 2.5 の直叩き対策の公式実装（Terraform） |
| aws-samples/serverless-patterns `apigw-http-api-waf-cloudfront-terraform` — https://github.com/aws-samples/serverless-patterns/tree/main/apigw-http-api-waf-cloudfront-terraform | 上記と同構成の Serverless Land パターン | 図解付き手順 |
| aws-samples/serverless-patterns `cloudfront-apigw-http-api-lambda-rust` — https://github.com/aws-samples/serverless-patterns/tree/main/cloudfront-apigw-http-api-lambda-rust | SAM で CloudFront → HTTP API → Lambda | SAM テンプレートの behavior/origin 定義 |
| aws-samples/serverless-patterns `sam-webapp-cognito` — https://github.com/aws-samples/serverless-patterns/tree/main/sam-webapp-cognito | SAM で Cognito + API + SPA | Cognito/authorizer 定義 |
| aws-samples/cookiecutter-aws-sam-python — https://github.com/aws-samples/cookiecutter-aws-sam-python | `sam init --location` で使う公式 cookiecutter（Powertools 同梱） | アプリ雛形の土台 |
| aws-samples/cookiecutter-aws-sam-pipeline — https://github.com/aws-samples/cookiecutter-aws-sam-pipeline | SAM 用 CI/CD 雛形 | 将来の CodePipeline 化 |
| Maoragai12/nx-aws-sam — https://github.com/Maoragai12/nx-aws-sam | SAM monorepo（Nx、共有 Layer、GitHub Actions） | monorepo の依存管理の一例（Node 系） |
| aws-samples/amazon-cloudfront-waf-secretsmanager — https://github.com/aws-samples/amazon-cloudfront-waf-secretsmanager | CloudFront origin 保護（カスタムヘッダ + WAF + Secrets Manager） | REST API/ALB 向けの原型 |
| 社内: example-portal | 独立スタック + Export 命名規約 `example-portal-{stack}-{resource}`、Route53/ACM は既存共有をパラメータ参照 | 本件の infra/apps/shared 分割の原型 |
| 社内: ref-project-a | Cognito PreSignUp（ドメイン制限）、SQS → notifier（SES/Slack）、CloudFront Function IP 制限 | PreSignUp と notifier はほぼ流用可 |

---

## 6. コスト概算（東京リージョン、月額、USD）

前提: 利用者 50 人、営業日 20 日、1 人 1 日 20 API 呼出（= 2 万 API req/月）、CloudFront 10 万 req/100MB、Lambda 4 万呼出 × 300ms × 256MB（= 3,000 GB-s）、月次バッチ 1 回 60 秒 × 1GB、DynamoDB 10 万書込/30 万読取・1GB、SES 200 通 + 添付 0.2GB、Cognito 50 MAU。

| サービス | 単価（単位） | 試算 | 備考 |
| --- | --- | --- | --- |
| AWS WAF | $5.00/WebACL・月、$1.00/ルール・月、$0.60/100万 req | **$6.06** | 固定費の大半。定額 Free プラン適用なら $0 |
| CloudFront | 常時無料枠 1TB 転送/1,000万 req/200万 CFF 呼出 | $0 | 超過しない |
| Cognito | Lite/Essentials とも 10,000 MAU/月まで無料、超過 Essentials $0.015/MAU | $0 | SAML/OIDC 連携は 50 MAU まで無料 |
| API Gateway HTTP API | $1.29/100万 req（3 億まで）、以降 $1.18 | $0.03 | 12 か月無料枠（100万/月）は新規アカウントのみ |
| Lambda | $0.20/100万 req、$0.0000166667/GB-s（x86）、常時無料枠 100万 req + 40万 GB-s | $0 | 無料枠内。無料枠を除いても約 $0.06 |
| DynamoDB オンデマンド | 書込 $0.715/100万 WRU、読込 $0.1425/100万 RRU、保存 $0.285/GB-月（25GB 無料） | $0.12 | |
| SES | $0.10/1,000 通、添付 $0.12/GB | $0.04 | 送信ドメイン検証は無料 |
| SQS / EventBridge Scheduler / S3 | SQS 100万 req 無料、Scheduler 1,400万呼出無料、S3 数 GB | <$0.2 | |
| CloudWatch Logs | 取込 約 $0.76/GB（東京） | <$0.5 | 保持 365 日 |
| SSM Parameter Store（Standard） | 無料 | $0 | Secrets Manager を使う場合 $0.40/シークレット・月 |
| **合計** | | **約 $7〜9/月**（WAF 除くと $1 前後） | |

- 定額 Free プランが適用できれば **約 $1〜2/月**。
- ユーザーや API 呼出が 10 倍になっても $10/月前後。コスト上の主な意思決定は「WAF を使うか、定額プランか、CloudFront Function か」だけ。

---

## 7. アプリ追加手順（10 ステップ）

1. `sam init --location ./templates/app --name <app>` で `apps/<app>/` を生成（app 名は小文字ケバブ、`/api/<app>` と SPA ルートに一致）。
2. `docs/CONTRACT.md` の SSM キーが `apps/<app>/template.yaml` の `Parameters.Default` と一致しているか確認（dev/prod の samconfig を含む）。
3. `src/app.py` に業務ロジックを実装（`APIGatewayHttpResolver(strip_prefixes=[API_PREFIX])`、`origin_verify` ミドルウェア、`notify.send(...)`）。テストを先に書く（TDD）。
4. `cd apps/<app> && sam build --use-container && sam deploy`（dev）。デプロイで `/example-backoffice/dev/apps/<app>/api-domain` が SSM に書かれる。
5. `curl https://<api-id>.execute-api.ap-northeast-1.amazonaws.com/api/<app>/health -H "Authorization: Bearer <token>" -H "x-origin-verify: <dev secret>"` で直接疎通確認。
6. `infra/edge/template.yaml` に雛形が出力した origin + behavior + パラメータ（2.7 の 10 行）を追記し、`cd infra/edge && sam deploy`（dev）。
7. `frontend/src/apps/<app>/` に画面を追加し `registry.ts` に登録、`scripts/deploy-frontend.sh dev` で S3 同期 + invalidation。
8. ブラウザで `https://backoffice-dev.example-cloud.com/<app>` から E2E 確認（ログイン → API → 通知受信）。
9. `docs/ADD_APP_CHECKLIST.md` のチェックを PR に転記し、レビュー（IAM 最小権限、SSM キー、タグ、ログ保持、DLQ）。
10. prod: `sam deploy --config-env prod` を app → edge → frontend の順に実行し、Slack へリリース通知。

---

## 8. 未確定事項・要検証

- CloudFront 定額 Free プラン: 社内アカウントの適格性（「AWS Free Tier 利用中」条件の解釈）、CloudFormation `AWS::CloudFront::Distribution` からのプラン指定可否、IP セットルールが Free の「5 ルール」に含まれるか。
- `Fn::GetStackOutput` の SAM CLI / cfn-lint 対応。対応していれば SSM レジストリの一部を置き換えられる。
- Cognito 機能プラン（Lite/Essentials）の MFA 方式差分。TOTP のみなら Lite で足りるはずだが公式表で再確認する（ドキュメントページが本調査では取得できず）。
- `AWS::SSM::Parameter::Value<String>` はデプロイ時に値を固定するため、core で Cognito を作り直した場合は全アプリの再デプロイが必要（手順書に明記）。
- Excel 生成の実行時間が 30 秒を超える場合の非同期化方式（SQS or Step Functions）。

---

## 9. 出典 URL 一覧

### AWS 公式ドキュメント
- CloudFront マネージド origin request policy（AllViewerExceptHostHeader = `b689b0a8-...`、API Gateway 向けの記述）: https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/using-managed-origin-request-policies.html
- CloudFront Authorization ヘッダ転送（re:Post KB）: https://repost.aws/knowledge-center/cloudfront-authorization-header
- CloudFront + API Gateway カスタムディストリビューション（re:Post KB）: https://repost.aws/knowledge-center/api-gateway-cloudfront-distribution
- CloudFront クォータ（behavior 75 / origin 100）: https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/cloudfront-limits.html
- CloudFront 定額プラン（機能表・許容量・制約）: https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/flat-rate-pricing-plan.html
- CloudFront 定額プラン発表（2025-11）: https://aws.amazon.com/about-aws/whats-new/2025/11/aws-flat-rate-pricing-plans
- CloudFront 料金: https://aws.amazon.com/cloudfront/pricing/
- CloudFront カスタムエラーページ: https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/GeneratingCustomErrorPages.html
- API Gateway REST API と HTTP API の比較（リソースポリシー・WAF 非対応）: https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-vs-rest.html
- HTTP API JWT authorizer（`aud`/`client_id` の扱い）: https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-jwt-authorizer.html
- HTTP API ステージ（`$default`）: https://docs.aws.amazon.com/apigateway/latest/developerguide/http-api-stages.html
- HTTP API を CloudFront 経由に限定する（AWS ブログ）: https://aws.amazon.com/blogs/networking-and-content-delivery/restricting-access-http-api-gateway-lambda-authorizer
- API Gateway 料金: https://aws.amazon.com/api-gateway/pricing/
- SAM `AWS::Serverless::HttpApi`（StageName 既定 `$default`、JWT 定義）: https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/sam-resource-httpapi.html
- SAM `sam init`（`--location`、cookiecutter 形式、`--no-input`）: https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/sam-cli-command-reference-sam-init.html
- CloudFormation ベストプラクティス（ライフサイクル/所有者で分割、クロススタック参照）: https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/best-practices.html
- CloudFormation SSM パラメータ型（`AWS::SSM::Parameter::Value<String>`、SecureString 不可）: https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/cloudformation-supplied-parameter-types.html
- CloudFormation Parameters 構文: https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/parameters-section-structure.html
- CloudFormation `Fn::GetStackOutput`: https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/intrinsic-function-reference-getstackoutput.html
- CloudFormation `Fn::ImportValue`: https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/intrinsic-function-reference-importvalue.html
- AWS WAF WebACL の関連付け（CloudFront は us-east-1、Cognito User Pool も可）: https://docs.aws.amazon.com/waf/latest/developerguide/web-acl-associating-aws-resource.html
- AWS WAF 料金: https://aws.amazon.com/waf/pricing/
- Cognito WebACL 関連付け: https://docs.aws.amazon.com/cognito/latest/developerguide/user-pool-waf.html
- Cognito Pre sign-up Lambda トリガー: https://docs.aws.amazon.com/cognito/latest/developerguide/user-pool-lambda-pre-sign-up.html
- Cognito 料金（Lite/Essentials 10,000 MAU 無料）: https://aws.amazon.com/cognito/pricing/
- SES 添付（v2 `SendEmail` Attachments、40MB、禁止拡張子）: https://docs.aws.amazon.com/ses/latest/dg/attachments.html
- SES 料金: https://aws.amazon.com/ses/pricing/
- SQS メッセージ上限 1 MiB（2025-08）: https://aws.amazon.com/about-aws/whats-new/2025/08/amazon-sqs-max-payload-size-1mib
- Lambda ベストプラクティス（冪等性、SQS 可視性タイムアウト）: https://docs.aws.amazon.com/lambda/latest/dg/best-practices.html
- Lambda 料金: https://aws.amazon.com/lambda/pricing/
- DynamoDB オンデマンド料金: https://aws.amazon.com/dynamodb/pricing/on-demand/
- Powertools for AWS Lambda (Python) Event Handler（`strip_prefixes`、正規表現可）: https://docs.aws.amazon.com/powertools/python/latest/core/event_handler/api_gateway/

### OSS / 事例
- aws-samples/integrate-httpapi-with-cloudfront-and-waf: https://github.com/aws-samples/integrate-httpapi-with-cloudfront-and-waf
- aws-samples/serverless-patterns（apigw-http-api-waf-cloudfront-terraform / cloudfront-apigw-http-api-lambda-rust / sam-webapp-cognito / cloudfront-keyvaluestore-apigw-routing-cdk）: https://github.com/aws-samples/serverless-patterns
- aws-samples/cookiecutter-aws-sam-python: https://github.com/aws-samples/cookiecutter-aws-sam-python
- aws-samples/cookiecutter-aws-sam-pipeline: https://github.com/aws-samples/cookiecutter-aws-sam-pipeline
- aws-samples/amazon-cloudfront-waf-secretsmanager: https://github.com/aws-samples/amazon-cloudfront-waf-secretsmanager
- Maoragai12/nx-aws-sam: https://github.com/Maoragai12/nx-aws-sam
- CustomErrorResponses が API 応答を書き換える事故報告: https://github.com/aws-samples/sample-collaborative-ai-dlc/issues/186
- Copier vs Cookiecutter 比較（テンプレート更新の有無）: https://copier.readthedocs.io/en/stable/comparisons/
- 社内参考: `/mnt/c/Users/devA/git/example-portal`（README.md, infra/template.yaml）、`/mnt/c/Users/devA/git/ref-project-a`（template.yaml, stacks/{api,notifier,queue,web,auth}/template.yaml, functions/notifier/）
