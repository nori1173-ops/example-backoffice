# 料金見積

| 項目 | 内容 |
| --- | --- |
| ドキュメント名 | COST-ESTIMATE.md（料金見積） |
| バージョン | v0.4 |
| 作成日 | 2026-08-28 |
| 改訂日 | 2026-09-02 |
| 作成者 | 弊社 開発部 |
| 対象読者 | 社内プロマネ・開発部・運用担当 |
| 上位設計書 | [`DESIGN.md`](DESIGN.md)（プラットフォーム） / [`apps/saas-a-billing/DESIGN.md`](apps/saas-a-billing/DESIGN.md)（第一弾アプリ） |
| Excel 版 | 公開版では省略（同じ内容は本書） |

> 社内保守管理サイト `example-backoffice`（基盤 + 第一弾アプリ `saas-a-billing`）の月額コスト見積。**単価は AWS Pricing MCP（Price List API）から取得**（取得日 2026-08-28、税別・USD）。dev / prod は同一 AWS アカウント（210000000001）・同一リージョン（ap-northeast-1）にスタック名の接尾辞で共存するため、**環境ごとに試算し合計も示す**。
>
> **v0.3（設計変更の反映）**: 社内 IP 制限の方式を **AWS WAF から CloudFront Function（viewer-request）へ変更**した（DESIGN §7）。WAF WebACL / ルール / リクエストの費用（1 環境あたり約 $6/月）が全シナリオから消え、月額合計は dev + prod で $12.56 → **$0.49** になった。**本システムの費用の 9 割超を占めていた固定費が無くなり、月額はほぼゼロになる。**

---

## 1. 前提条件

| 項目 | 値 |
| --- | --- |
| リージョン（主） | ap-northeast-1（東京） |
| リージョン（副） | us-east-1（CloudFront 用 ACM 証明書のみ。**WAF を廃止したため WebACL は無い**） |
| AWS アカウント | 210000000001（dev / prod 同居） |
| 料金基準 | **AWS Pricing MCP（Price List API）**（2026-08-28 取得、税別・USD） |
| 為替レート | 1 USD = 155 JPY（社内見積用の固定値。実請求は変動する） |
| 無料利用枠 | **常時無料枠（Always Free）のみ適用**。12 か月無料枠は既存アカウントのため適用しない |
| 利用者 | 一般利用者 少人数（CS 部・開発部）、管理者 数人。Cognito MAU は 10 人程度 |
| API 呼出 | prod 月 20,000 リクエスト（平均 200 ms / メモリ 512 MB） |
| CloudFront | **従量課金を既定**とする（DESIGN §16。定額プランは運用開始後の任意の最適化）。prod 月 100,000 リクエスト / 1 GB 転送 |
| 入口制御 | **CloudFront Function（viewer-request）で社内 IP / CIDR を判定し、不一致は 403 を返す**（DESIGN §7）。**AWS WAF は使わない**。関数は SPA フォールバックと IP 判定を兼ね、**全 behavior（`/*` と `/api/{app}/*`）に関連付ける**ため、実行回数はディストリビューションの総リクエスト数と等しい |
| 証明書 / DNS | ACM は既存ワイルドカード証明書を再利用（無料）。Route 53 は既存共有ゾーンにレコード追加のみ（**ゾーン料金は計上しない**。Alias クエリは無料） |
| 認証 | Cognito User Pool `UserPoolTier: LITE`、MAU 10 人程度（常時無料枠 10,000 MAU 内） |
| 通知 | SQS 標準キュー → notifier Lambda → SES v2（添付 1 MB 程度のメール、prod 月 5 通）+ Slack Bot token（Slack 側は無料） |
| 課金バッチ | EventBridge Scheduler（毎日 06:00 起動、月 30 回）→ trigger Lambda → Step Functions Standard（Init → Map(CollectTenant, MaxConcurrency 2) → Aggregate） |
| バッチ規模（当面） | 50 テナント・500 地点。CollectTenant 50 回 × 約 30 秒 × 1 GB、Aggregate 60 秒 × 1 GB、**月 1 実行（再実行込みで月 2 実行として計上）** |
| バッチ規模（将来） | 3,000 地点・100 テナント。CollectTenant 100 回 × 約 120 秒 × 1 GB |
| データ | DynamoDB オンデマンド（RUN / SETTINGS / PRICE、数百アイテム、1 GB 未満）、S3（SPA 配信用 + 添付バケット。Excel / result.json / parts で月 10 MB 増、削除しない） |
| ログ | CloudWatch Logs 月 200 MB 取込・**保持 365 日**（DESIGN §12.4）。定常状態のストレージは 200 MB × 12 か月 = **2.4 GB / 環境** |
| 監視 | CloudWatch アラーム 5 個 / 環境（core の notify DLQ 1 本 + saas-a-billing 4 本） |
| 暗号化・秘密情報 | KMS カスタマーキーは使わない（SSE-S3 / DynamoDB AWS owned key）。Secrets Manager は使わない。SSM Parameter Store は Standard パラメータ（無料） |

---

## 2. リソース構成

| ID | リソース | 仕様 | 根拠 |
| --- | --- | --- | --- |
| R1 | CloudFront ディストリビューション | 1 本。SPA（S3 + OAC）と全アプリ API を同居。従量課金 | DESIGN §5.2 / §16 |
| R2 | CloudFront Function（viewer-request） | `cloudfront-js-2.0`。**社内 IP / CIDR 判定（不一致は 403）+ SPA フォールバック**。全 behavior に関連付け | DESIGN §7 / §5.4 |
| R3 | ACM 証明書 | 既存ワイルドカード証明書を参照（us-east-1、新規発行なし） | 確定事項 |
| R4 | Route 53 レコード | 既存共有ゾーン `example-cloud.com` に Alias A / AAAA を追加 | DESIGN §5.1 |
| R5 | S3（SPA） | Standard、SSE-S3、OAC 経由のみ | DESIGN §11.2 |
| R6 | S3（attachments） | Standard、SSE-S3。`{app}/{yyyy}/{mm}/` に Excel / result.json / parts | DESIGN §11.2 |
| R7 | Cognito User Pool | `UserPoolTier: LITE`、TOTP MFA（任意）、`@example.co.jp` 限定セルフサインアップ | DESIGN §6.1 / 確定事項 |
| R8 | API Gateway HTTP API | `$default` ステージ、Cognito JWT authorizer。アプリごとに 1 API | DESIGN §5.3 |
| R9 | Lambda（api） | Python 3.13、512 MB、timeout 29 秒 | saas-a-billing DESIGN §5.5 |
| R10 | Lambda（trigger） | Python 3.13、256 MB、timeout 60 秒 | 同上 |
| R11 | Lambda（init / collect_tenant / aggregate / mark_failed） | Python 3.13、1024 MB、timeout 900 秒 | 同上 |
| R12 | Lambda（notifier / PreSignUp） | Python 3.13、512 MB、timeout 60 秒 | DESIGN §8.1 |
| R13 | Lambda Layer（shared / ExamplePowertoolsPython） | 追加課金なし | DESIGN §10 |
| R14 | Step Functions ステートマシン | Standard `saas-a-billing-run`。Init → Map(MaxConcurrency 2) → Aggregate | saas-a-billing DESIGN §5.2 |
| R15 | EventBridge Scheduler | `cron(0 6 * * ? *)` / Asia/Tokyo。月 30 回 | 同上 |
| R16 | SQS（notify-queue + notify-dlq） | 標準キュー。notifier Lambda のイベントソース | DESIGN §8.1 |
| R17 | DynamoDB | オンデマンド（PAY_PER_REQUEST）。アプリ 1 テーブル + notifier 冪等性テーブル | DESIGN §11.1 |
| R18 | SES v2 | EmailIdentity（Easy DKIM）、`Simple + Attachments` で Excel 添付 | DESIGN §8.4 |
| R19 | SNS | Ops トピック `example-backoffice-ops-{env}`。全アラームの通知先（基盤担当メール） | DESIGN §8.1 |
| R20 | CloudWatch Logs / アラーム | Lambda / HTTP API アクセスログ（**保持 365 日**）、アラーム 5 個 | DESIGN §12.4 |
| R21 | SSM Parameter Store | 契約レジストリ + SecureString（Standard パラメータ） | DESIGN §4 |

- **AWS WAF（WebACL / IPSet）は構成に含まない**（v0.3 の設計変更）。IP 制限は R2 の CloudFront Function が担う。us-east-1 に残るのは ACM 証明書のみ。
- KMS カスタマーキー・Secrets Manager・Route 53 ホストゾーン・ACM 新規発行は**本見積のスコープ外**（既存共有リソースの参照、または不使用）。
- Slack は Bot token 方式で AWS 課金なし（Slack 側も無料プランの範囲）。

---

## 3. 単価一覧（AWS Pricing MCP 取得値）

すべて AWS Pricing MCP（Price List API）から取得（2026-08-28）。sku は §10 付録に一覧する。

| サービス | 単価 | 単位 | リージョン |
| --- | ---: | --- | --- |
| **CloudFront Functions** | **$0.10** | **/100 万実行（$0.0000001/実行）。最初の 200 万実行は $0** | Global |
| CloudFront HTTPS リクエスト | **$0.012** | /10,000 リクエスト（日本） | Global（Japan） |
| CloudFront データ転送 out | **$0.114** | /GB（最初の 10 TB、日本） | Global（Japan） |
| API Gateway HTTP API | **$1.29** | /100 万リクエスト（最初の 3 億） | ap-northeast-1 |
| Lambda リクエスト | **$0.20** | /100 万リクエスト | ap-northeast-1 |
| Lambda 実行時間 | **$0.0000166667** | /GB-秒（Tier-1、60 億 GB-秒まで） | ap-northeast-1 |
| Step Functions（Standard） | **$0.025** | /1,000 状態遷移（$0.000025/遷移） | ap-northeast-1 |
| EventBridge Scheduler | **$1.25** | /100 万起動（最初の 1,400 万起動は $0） | ap-northeast-1 |
| DynamoDB 書込（オンデマンド） | **$0.715** | /100 万 WRU | ap-northeast-1 |
| DynamoDB 読込（オンデマンド） | **$0.1425** | /100 万 RRU | ap-northeast-1 |
| DynamoDB ストレージ | **$0.285** | /GB-月（最初の 25 GB は $0） | ap-northeast-1 |
| S3 Standard ストレージ | **$0.025** | /GB-月（最初の 50 TB） | ap-northeast-1 |
| S3 リクエスト（PUT/COPY/POST/LIST） | **$0.0047** | /1,000 リクエスト | ap-northeast-1 |
| S3 リクエスト（GET その他） | **$0.0037** | /10,000 リクエスト | ap-northeast-1 |
| SQS（標準キュー） | **$0.40** | /100 万リクエスト（Tier1） | ap-northeast-1 |
| SES 送信 | **$0.10** | /1,000 宛先（$0.0001/宛先） | ap-northeast-1 |
| SES 添付 | **$0.12** | /GB | ap-northeast-1 |
| SNS メール通知 | **$2.00** | /100,000 通知（最初の 1,000 通知は $0） | ap-northeast-1 |
| Cognito Lite MAU | **$0.0055** | /MAU（最初の 90,000 MAU の従量単価） | ap-northeast-1 |
| CloudWatch Logs 取込（Standard） | **$0.76** | /GB | ap-northeast-1 |
| CloudWatch Logs ストレージ | **$0.033** | /GB-月 | ap-northeast-1 |
| CloudWatch アラーム（標準分解能） | **$0.10** | /アラームメトリクス・月 | ap-northeast-1 |
| Route 53 Alias クエリ | **$0.00** | /クエリ（AWS リソース宛 Alias は無料） | Global |
| ACM パブリック証明書 | **$0.00** | — | us-east-1 |
| SSM Parameter Store（Standard） | **$0.00** | — | ap-northeast-1 |

> **単位の換算に注意**: CloudFront Functions は「/100 万実行」、CloudFront リクエストは「/10,000 リクエスト」、S3 の PUT 系は「/1,000 リクエスト」、GET 系は「/10,000 リクエスト」、Step Functions は「/1,000 状態遷移」、SES は「/1,000 宛先」である。本書の試算はすべて 1 単位あたりの単価（$/実行、$/リクエスト 等）に換算したうえで計算している。
>
> AWS WAF の単価（WebACL $5.00/月、ルール $1.00/月、リクエスト $0.60/100 万）は本見積では未計上。将来 WAF を追加する場合の試算に §8.2 で用いる。

---

## 4. 固定費

リクエスト数に依存しないコスト。dev / prod ともに同額。

| リソース | 単価 | 数量 | 月額（USD） | 月額（JPY） |
| --- | ---: | ---: | ---: | ---: |
| CloudWatch アラーム | $0.10/月 | 5 | $0.50 | ¥78 |
| ACM 証明書（既存を参照） | — | 1 | $0.00 | ¥0 |
| Route 53 ホストゾーン（既存共有） | — | — | $0.00 | ¥0 |
| SSM Parameter Store（Standard） | — | 約 15 | $0.00 | ¥0 |
| **固定費 合計（1 環境あたり、無料枠適用前）** | | | **$0.50** | **¥78** |

- **v0.3 で WAF（WebACL $5.00 + ルール $1.00 = $6.00/環境）が固定費から消えた**。IP 制限は CloudFront Function（実行従量、200 万実行まで無料）に置き換わったため、本システムには**実質的な月額固定費が無い**。
- CloudWatch アラームはアカウント全体で 10 個まで常時無料。dev 5 + prod 5 = **ちょうど 10 個**のため無料枠適用後の固定費は $0.00 だが、**余裕はゼロ**である。アラームを 1 本足すか、同一アカウントの他システムがアラームを持つと 1 個あたり $0.10/月が発生する。
- **API Gateway HTTP API の詳細メトリクス**（`apps/saas-a-billing/template.yaml` の `DefaultRouteSettings.DetailedMetricsEnabled: true`）はルート単位のメトリクスを CloudWatch **カスタムメトリクス**として発行し、$0.30/メトリクス・月（最初の 10,000 個・ap-northeast-1）で課金される（無料枠なし）。ルートは `ANY /api/saas-a-billing/{proxy+}` の 1 本・1 ルートあたり 5 メトリクス（Count / 4xx / 5xx / Latency / IntegrationLatency）で **約 $1.50/月・環境**。本見積の表には未計上で、アプリ（＝ HTTP API）を増やすとその数に比例して増える。

---

## 5. 従量費

### 5.1 シナリオ別の前提数量

| 項目 | dev | prod（当面: 50 テナント・500 地点） | 将来（3,000 地点・100 テナント） |
| --- | ---: | ---: | ---: |
| API リクエスト / 月 | 5,000 | 20,000 | 40,000 |
| Lambda 呼出回数 / 月 | 5,068 | 20,139 | 40,239 |
| Lambda GB-秒 / 月 | 1,513 | 5,225 | 28,525 |
| CloudFront リクエスト / 月 | 20,000 | 100,000 | 200,000 |
| **CloudFront Functions 実行回数 / 月** | **20,000** | **100,000** | **200,000** |
| CloudFront データ転送 out / 月 | 0.3 GB | 1.0 GB | 2.0 GB |
| バッチ実行回数 / 月 | 4 | 2 | 2 |
| CollectTenant 実行回数 / 月 | 20（5 テナント × 4 回） | 100（50 テナント × 2 回） | 200（100 テナント × 2 回） |
| Step Functions 状態遷移 / 月 | 60 | 120 | 220 |
| DynamoDB WRU / RRU | 2,000 / 10,000 | 5,000 / 40,000 | 20,000 / 100,000 |
| DynamoDB ストレージ | 0.2 GB | 0.5 GB | 1.0 GB |
| S3 ストレージ | 0.08 GB | 0.15 GB | 0.5 GB |
| S3 PUT / GET | 1,500 / 2,000 | 2,000 / 5,000 | 4,000 / 10,000 |
| SQS リクエスト / 月 | 650,000 | 650,000 | 650,000 |
| SES 送信 / 添付 | 10 通 / 10 MB | 5 通 / 5 MB | 5 通 / 15 MB |
| Cognito MAU | 3 | 10 | 20 |
| CloudWatch Logs 取込 / 月 | 0.2 GB | 0.2 GB | 0.5 GB |
| CloudWatch Logs ストレージ（保持 365 日） | 2.4 GB | 2.4 GB | 6.0 GB |

> **CloudFront Functions の実行回数**: IP 判定を viewer-request の関数で行うため、関数を SPA 用の default behavior だけでなく **`/api/{app}/*` の behavior にも関連付ける**。したがって実行回数は**ディストリビューションの総リクエスト数と等しい**（dev 20,000 / prod 100,000 / 将来 200,000）。常時無料枠は 200 万実行/月（アカウント共有）で、prod でも枠の 5% しか使わない。
>
> **SQS リクエスト数について**: notifier Lambda のイベントソースマッピングは、キューが空でも 20 秒ロングポーリングを 5 並列で継続するため、実メッセージ数（月数通）とは無関係に **1 環境あたり約 65 万リクエスト/月** が発生する（20 秒 × 5 並列 = 15 リクエスト/分 × 43,200 分）。設計書に記載のない前提として本見積で置いた数値である。
>
> **CloudWatch Logs ストレージについて**: 保持 365 日のため、定常状態では月間取込量の 12 か月分が保存され続ける（0.2 GB × 12 = 2.4 GB / 環境、将来シナリオは 0.5 GB × 12 = 6.0 GB）。運用開始 1 年目は徐々に積み上がるため、本見積は**運用 1 年経過後の定常状態**を示す。

### 5.2 サービス別コスト（無料利用枠適用前）

| サービス | 算出根拠（prod・当面） | dev | prod | 将来 |
| --- | --- | ---: | ---: | ---: |
| CloudFront リクエスト | 100,000 × $0.0000012 | $0.02 | $0.12 | $0.24 |
| CloudFront データ転送 | 1.0 GB × $0.114 | $0.03 | $0.11 | $0.23 |
| **CloudFront Functions（IP 判定 + SPA フォールバック）** | 100,000 実行 × $0.0000001 | $0.00 | $0.01 | $0.02 |
| API Gateway（HTTP API） | 20,000 × $0.00000129 | $0.01 | $0.03 | $0.05 |
| Lambda リクエスト | 20,139 × $0.0000002 | $0.00 | $0.00 | $0.01 |
| Lambda 実行時間 | 5,225 GB-秒 × $0.0000166667 | $0.03 | $0.09 | $0.48 |
| Step Functions（Standard） | 120 遷移 × $0.000025 | $0.00 | $0.00 | $0.01 |
| EventBridge Scheduler | 30 起動（1,400 万まで $0） | $0.00 | $0.00 | $0.00 |
| DynamoDB 書込 | 5,000 WRU × $0.000000715 | $0.00 | $0.00 | $0.01 |
| DynamoDB 読込 | 40,000 RRU × $0.0000001425 | $0.00 | $0.01 | $0.01 |
| DynamoDB ストレージ | 0.5 GB（25 GB まで $0） | $0.00 | $0.00 | $0.00 |
| S3 ストレージ | 0.15 GB × $0.025 | $0.00 | $0.00 | $0.01 |
| S3 リクエスト（PUT 系） | 2,000 × $0.0000047 | $0.01 | $0.01 | $0.02 |
| S3 リクエスト（GET 系） | 5,000 × $0.00000037 | $0.00 | $0.00 | $0.00 |
| SQS | 650,000 × $0.0000004 | $0.26 | $0.26 | $0.26 |
| SES 送信 | 5 通 × $0.0001 | $0.00 | $0.00 | $0.00 |
| SES 添付 | 0.005 GB × $0.12 | $0.00 | $0.00 | $0.00 |
| SNS メール通知 | 2 通（1,000 通まで $0） | $0.00 | $0.00 | $0.00 |
| Cognito（Lite） | 10 MAU × $0.0055 | $0.02 | $0.06 | $0.11 |
| CloudWatch Logs 取込 | 0.2 GB × $0.76 | $0.15 | $0.15 | $0.38 |
| CloudWatch Logs ストレージ | 2.4 GB × $0.033（保持 365 日） | $0.08 | $0.08 | $0.20 |
| CloudWatch アラーム | 5 × $0.10 | $0.50 | $0.50 | $0.50 |
| Route 53（Alias クエリ） | Alias は無料 | $0.00 | $0.00 | $0.00 |
| ACM / SSM（Standard） | 無料 | $0.00 | $0.00 | $0.00 |
| **合計（無料枠適用前）** | | **$1.12** | **$1.44** | **$2.54** |

> 各行はセント単位に丸めて表示しているため、表示値を足し上げた額と合計欄が最大 $0.01 ずれることがある。合計欄は丸め前の値で計算している。
>
> AWS WAF の行（WebACL / ルール / リクエスト）は v0.3 で**削除**した。従来は 1 環境あたり $6.01〜$6.12/月を占めていた。

### 5.3 計算根拠

```
[CloudFront Functions（IP 判定 + SPA フォールバック）]
  関数は viewer-request に 1 本。default (/*) と /api/{app}/* の両 behavior に関連付ける
  → 実行回数 = ディストリビューションの総リクエスト数
  dev    :  20,000 実行 × $0.0000001 = $0.002
  prod   : 100,000 実行 × $0.0000001 = $0.010
  将来   : 200,000 実行 × $0.0000001 = $0.020
  常時無料枠 200 万実行/月（アカウント共有）。dev + prod = 120,000（将来 220,000）で枠内
  → 実費 $0.00
  ※ 旧方式（AWS WAF）は WebACL $5.00 + ルール $1.00 + リクエスト課金で 1 環境あたり約 $6/月だった

[Lambda 実行時間 — prod（当面: 50 テナント・500 地点）]
  api            : 20,000 回 × 0.2 秒 × 0.5 GB =   2,000 GB-秒
  trigger        :     30 回 × 5   秒 × 0.25 GB =     38 GB-秒
  Init           :      2 回 × 30  秒 × 1 GB   =     60 GB-秒
  CollectTenant  :      2 回 × 50 テナント × 30 秒 × 1 GB = 3,000 GB-秒
  Aggregate      :      2 回 × 60  秒 × 1 GB   =    120 GB-秒
  notifier       :      5 回 × 3   秒 × 0.5 GB =      8 GB-秒
  ------------------------------------------------------------
  合計 5,225 GB-秒 × $0.0000166667 = $0.087  → 常時無料枠 400,000 GB-秒 内のため実費 $0.00

[Lambda 実行時間 — 将来（3,000 地点・100 テナント）]
  CollectTenant  :      2 回 × 100 テナント × 120 秒 × 1 GB = 24,000 GB-秒
  Init / Aggregate / api / trigger / notifier          =  4,525 GB-秒
  ------------------------------------------------------------
  合計 28,525 GB-秒 × $0.0000166667 = $0.475 → dev と合わせても 400,000 GB-秒 内のため実費 $0.00

[Step Functions（Standard）— prod]
  1 実行あたりの状態遷移 ≈ Init 1 + Map 1 + CollectTenant 50 + Aggregate 1 + 終端 1 ≈ 54
  余裕を見て 60 遷移 / 実行 × 2 実行 = 120 遷移
  120 × $0.000025 = $0.003 → 常時無料枠 4,000 遷移 内のため実費 $0.00

[CloudFront — prod]
  リクエスト     100,000 × $0.0000012 = $0.12   （$0.012 / 10,000 リクエストを換算）
  データ転送 out     1 GB × $0.114     = $0.114
  合計 $0.234  ← 無料枠適用後の月額で最大の項目

[SQS — 1 環境あたり]
  notifier の ESM ロングポーリング: 20 秒 × 5 並列 = 15 リクエスト/分
  15 × 60 分 × 24 時間 × 30 日 = 648,000 ≈ 650,000 リクエスト
  650,000 × $0.0000004 = $0.26
  dev + prod = 1,300,000 リクエスト。常時無料枠 100 万を差し引き 300,000 × $0.0000004 = $0.12

[CloudWatch Logs — 保持 365 日]
  取込      0.2 GB × $0.76  = $0.152 / 環境
  ストレージ 0.2 GB × 12 か月 = 2.4 GB × $0.033 = $0.079 / 環境
  常時無料枠は「取込 + アーカイブ保存 + Insights スキャン」の合計 5 GB/月（アカウント共有）
    dev + prod = 取込 0.4 GB + 保存 4.8 GB = 5.2 GB → 0.2 GB 超過
    無料枠を取込（0.4 GB）→ 保存（4.6 GB）の順に充当し、超過 0.2 GB を保存料金で計上
    0.2 GB × $0.033 = $0.0066 → $0.01（prod に計上）
  将来シナリオ: dev 2.6 GB + prod 6.5 GB = 9.1 GB → 4.1 GB 超過
    4.1 GB × $0.033 = $0.135 → $0.14（prod に計上）
```

---

## 6. 無料利用枠の適用

適用するのは**常時無料枠（Always Free）のみ**。12 か月無料枠（S3 5 GB、SES 3,000 メッセージ等）は既存アカウントのため適用しない。無料枠は**アカウント全体で共有**されるため、dev + prod を合算して判定する。

| サービス | 常時無料枠 | dev + prod の使用量 | 判定 | 出典 |
| --- | --- | --- | --- | --- |
| **CloudFront Functions** | **200 万実行/月** | **120,000（将来 220,000）** | **枠内 → $0**（枠の 6%） | MCP（sku QAZUUEJG8GARJCET の $0 レンジ） |
| Lambda リクエスト | 100 万リクエスト/月 | 25,207 | 枠内 → $0 | Web 取得（aws.amazon.com/lambda/pricing/） |
| Lambda 実行時間 | 400,000 GB-秒/月 | 6,738 GB-秒（将来 30,038） | 枠内 → $0 | Web 取得（同上） |
| SQS | 100 万リクエスト/月 | 1,300,000 | **超過 300,000** → $0.12 | Web 取得（aws.amazon.com/sqs/pricing/） |
| CloudWatch Logs | 5 GB/月（取込 + アーカイブ保存 + Insights スキャン） | 5.2 GB（将来 9.1 GB） | **超過 0.2 GB** → $0.01（将来は 4.1 GB → $0.14） | Web 取得（aws.amazon.com/cloudwatch/pricing/） |
| Cognito（Lite） | 10,000 MAU/月（無期限） | 13 | 枠内 → $0 | Web 取得（aws.amazon.com/cognito/pricing/） |
| Step Functions（Standard） | 4,000 状態遷移/月（無期限） | 180 | 枠内 → $0 | Web 取得（aws.amazon.com/step-functions/pricing/） |
| CloudWatch アラーム | 10 アラームメトリクス/月 | 10（dev 5 + prod 5） | **枠ちょうど** → $0（余裕なし） | Web 取得（aws.amazon.com/cloudwatch/pricing/） |
| EventBridge Scheduler | 1,400 万起動/月 | 60 | 枠内 → $0 | MCP（sku 29C3758CBCYNXH8B の $0 レンジ） |
| DynamoDB ストレージ | 25 GB-月 | 0.7 GB | 枠内 → $0 | MCP（sku 2HPSAWPXJ2JJJ6XY の $0 レンジ） |
| SNS | 100 万リクエスト + 1,000 メール通知/月 | 4 通 | 枠内 → $0 | MCP（sku U7N84S2RCJJJKURJ / RFRANPTXHKXT5NFV の $0 レンジ） |
| CloudFront リクエスト / データ転送 | **適用しない**（下の注記） | — | 従量課金で計上 | — |

> **CloudFront Functions の無料枠**: 200 万実行/月はアカウント全体で共有される常時無料枠であり、単価表（sku `QAZUUEJG8GARJCET`）の第 1 レンジ（0〜2,000,000 実行 = $0）として Price List API 上に定義されている。**IP 制限を CloudFront Function で行っても、prod の 10 万実行は枠の 5%**（dev + prod で 6%）に過ぎず、リクエストが 20 倍（月 200 万）になるまで課金は発生しない。
>
> **CloudFront の無料枠を適用しない理由**: 従来の常時無料枠（1 TB 転送 / 1,000 万リクエスト）は、現在の AWS 公式料金ページでは定額プラン（Free プラン: 1,000,000 リクエスト / 100 GB 転送 / S3 5 GB）に置き換えられた形で提示されており、従量課金のまま従来の常時無料枠が適用されるかを確認できなかった。DESIGN §16 のとおり **CloudFront は従量課金を既定**とする方針であり、本見積も**保守側に倒して CloudFront を全額従量で計上**する。従来の常時無料枠が適用される場合は prod で月 -$0.23、dev で月 -$0.06 となり、**月額はほぼ $0 になる**。

### 無料枠適用後のサービス別内訳

| サービス | dev | prod | 将来（prod） |
| --- | ---: | ---: | ---: |
| CloudFront（リクエスト + データ転送） | $0.06 | $0.23 | $0.47 |
| SQS（無料枠超過分） | $0.00 | $0.12 | $0.12 |
| API Gateway（HTTP API） | $0.01 | $0.03 | $0.05 |
| S3（ストレージ + リクエスト） | $0.01 | $0.02 | $0.04 |
| DynamoDB（WRU + RRU） | $0.00 | $0.01 | $0.03 |
| CloudWatch Logs（無料枠超過分） | $0.00 | $0.01 | $0.14 |
| SES（送信 + 添付） | $0.00 | $0.00 | $0.00 |
| **CloudFront Functions** | **$0.00** | **$0.00** | **$0.00** |
| Lambda / Step Functions / EventBridge / Cognito / CW アラーム / SNS / Route 53 / ACM / SSM | $0.00 | $0.00 | $0.00 |
| **合計** | **$0.08** | **$0.41** | **$0.84** |

> SQS と CloudWatch Logs の無料枠超過分はアカウント全体で発生する費用であり、本表では便宜上 prod 側に計上している。各行はセント単位に丸めて表示しているため、表示値の合計と合計欄が最大 $0.01 ずれることがある。

---

## 7. 月額合計

| 区分 | dev | prod | dev + prod |
| --- | ---: | ---: | ---: |
| 無料枠適用前（USD） | $1.12 | $1.44 | **$2.55** |
| 無料枠適用前（JPY） | ¥174 | ¥223 | **¥395** |
| **無料枠適用後（USD）** | **$0.08** | **$0.41** | **$0.49** |
| **無料枠適用後（JPY）** | **¥12** | **¥64** | **¥76** |
| 年額換算（JPY、無料枠適用後） | ¥148 | ¥766 | **¥914** |

### 将来（3,000 地点・100 テナント）に拡大した場合

| 区分 | dev | prod（将来） | dev + prod |
| --- | ---: | ---: | ---: |
| 無料枠適用前（USD） | $1.12 | $2.54 | **$3.66** |
| **無料枠適用後（USD）** | **$0.08** | **$0.84** | **$0.92** |
| **無料枠適用後（JPY）** | **¥12** | **¥130** | **¥143** |
| 年額換算（JPY、無料枠適用後） | ¥148 | ¥1,564 | **¥1,712** |

### v0.2（WAF 方式）からの差分

| 区分 | v0.2（AWS WAF） | v0.3（CloudFront Function） | 差分 |
| --- | ---: | ---: | ---: |
| dev（無料枠適用後） | $6.09 | $0.08 | **-$6.01** |
| prod（無料枠適用後） | $6.47 | $0.41 | **-$6.06** |
| dev + prod（無料枠適用後） | $12.56 | $0.49 | **-$12.07（-96%）** |
| dev + prod（将来、無料枠適用後） | $13.05 | $0.92 | **-$12.13（-93%）** |
| 年額（dev + prod、無料枠適用後） | ¥23,364 | ¥914 | **-¥22,450** |

### コスト構成比（prod・当面、無料枠適用後）

| 順位 | リソース | 月額（USD） | 構成比 |
| --- | --- | ---: | ---: |
| 1 | CloudFront（リクエスト + データ転送） | $0.23 | 56.8% |
| 2 | SQS（無料枠超過分） | $0.12 | 29.1% |
| 3 | API Gateway | $0.03 | 6.3% |
| 4 | S3（ストレージ + リクエスト） | $0.02 | 3.6% |
| 5 | DynamoDB | $0.01 | 2.3% |
| 6 | CloudWatch Logs（無料枠超過分） | $0.01 | 1.6% |
| 7 | SES | $0.00 | 0.3% |

> **固定費が消え、月額は実質ゼロになった**。prod で月 $0.41（約 ¥64）、dev + prod でも月 $0.49（約 ¥76）。地点数が 500 → 3,000 に 6 倍になっても dev + prod で月 $0.92（約 ¥143）にとどまる。残るコストの過半は CloudFront の従量分であり、これも従来の常時無料枠が適用されれば消える。

---

## 8. 注意点・コスト変動要因

### 8.1 従量のため変動しうる項目

| # | 項目 | 変動要因 | 影響 |
| --- | --- | --- | --- |
| 1 | CloudFront Functions 実行回数 | ディストリビューションの総リクエスト数と等しい。常時無料枠は 200 万実行/月（アカウント共有） | prod の 10 万実行は枠の 5%。**リクエストが 20 倍になるまで $0**。超過分は $0.10/100 万実行（月 400 万実行でも $0.20/月） |
| 2 | CloudFront データ転送 | Excel 添付を SPA 経由でダウンロードする運用に変えると転送量が増える | 10 GB/月で +$1.14/月。無料枠適用後の月額で最大の変動要因 |
| 3 | CloudWatch Logs の取込量 | 保持 365 日のため、取込量の増加はストレージに 12 倍で効く。ログレベルを DEBUG にして取込が 1 GB/月・環境になると、ストレージは 12 GB/環境まで積み上がり dev + prod で合計 26 GB | 無料枠 5 GB を超えた 21 GB を保存料金で見て **+$0.69/月**。取込側も課金対象になれば更に増える |
| 4 | CloudWatch Logs の保持期間 | 保持を 90 日に短縮するとストレージが 2.4 GB → 0.6 GB / 環境になり、dev + prod 合計 1.6 GB で無料枠 5 GB 内に収まる | prod −$0.01/月、将来シナリオで prod −$0.14/月 |
| 5 | SQS | notifier の ESM ポーリング数。Lambda 側のスケール挙動で増減する | 2 倍になると +$0.26/月。**無料枠適用後の月額では CloudFront に次ぐ 2 番目の項目** |
| 6 | Lambda 実行時間 | CollectTenant の 1 テナントあたり所要時間。saas-a-billing DESIGN §6 の実測は 50〜60 秒（本見積は確定事項に従い 30 秒で計上） | 60 秒に倍増すると prod で +$0.05/月（無料枠内のため実費は $0 のまま） |
| 7 | Cognito MAU | 利用者が少人数に増えても 10,000 MAU の常時無料枠内 | 影響なし |
| 8 | 為替レート | 1 USD = 155 JPY の固定値で換算している | 月額が小さいため影響は軽微（10 円の変動で dev + prod 月 ±¥5 程度） |

### 8.2 構成変更によるコスト影響

- **IP 制限は CloudFront Function 方式を採用済み**（DESIGN §7、v0.3）。viewer-request の関数で `event.viewer.ip` を許可 CIDR と照合し、不一致は 403 を返す。**AWS WAF を使わないことで 1 環境あたり月 約 $6、dev + prod で月 約 $12（年 約 ¥22,450）を削減している。** トレードオフは監査性で、WAF のサンプルリクエストやブロック件数メトリクスは得られない（CloudFront の標準ログで代替する）。
- **WAF を後から付ける場合の追加費用**: CloudFront スコープの WebACL 1 個 + IP set ルール 1 本で **1 環境あたり月 約 $6.06**（WebACL $5.00 + ルール $1.00 + リクエスト 100,000 × $0.0000006 = $0.06）。dev + prod では **月 約 $12.07**。ブロック件数の可視化や WAF ログが必要になった時点で追加できる（単価は §10 付録の awswaf sku を参照）。
- **Cognito User Pool にリージョナル WebACL を付ける場合**（DESIGN §5.5）: **月 +$6.00**（WebACL $5.00 + ルール $1.00）。`cognito-idp` エンドポイントは CloudFront を経由しないため、CloudFront Function では保護できない点に注意する。
- **CloudFront 定額プランを採用した場合**: Free プラン（$0/月、1,000,000 リクエスト / 100 GB 転送 / S3 5 GB）が適用できれば prod の CloudFront $0.23 が不要になり **月 -$0.23**（dev -$0.06）。ただし DESIGN §16 のとおり定額プランは**既定では採らない**（(1) CloudFormation / SAM から指定できず CLI・API による後処理が必要、(2) アクセスログと WAF ログが出ず DESIGN §12.4 のログ要件を満たせない、(3) IP set ベースのルールが Free の 5 ルール枠でどう扱われるか公式に未記載）。運用開始後の**任意の最適化**として `aws pricing-plan-manager` による加入手順が存在する。Pro プラン（$15/月）を選ぶと逆に **月 +$14.6 程度の増**となる。
- **将来 MFA を必須へ戻して IP 制限を外した場合**（DESIGN §7.2）: CloudFront Function は 200 万実行まで無料のため、**コスト影響はない**（WAF 方式では -$6/環境の削減効果があったが、v0.3 では既にゼロ）。関数から IP 判定ロジックを外しても SPA フォールバックのために関数自体は残す。

### 8.3 その他

- 無料利用枠は **AWS アカウント全体で共有**される。アカウント 210000000001 で他システムが Lambda / CloudWatch Logs / SQS / CloudFront Functions を利用している場合、本見積の「無料枠適用後」金額は成立せず「無料枠適用前」に近づく。**最大でも dev + prod で月 $2.55（¥395）**を上限として見ればよい。
- CloudWatch Logs のストレージは運用開始から 12 か月かけて積み上がる。1 年目の平均は本見積の約半分になる。
- 本見積はリクエスト処理のコストのみで、開発時の試行錯誤（CloudFormation デプロイ、E2E テスト実行）による一時的な増分は含まない。
- SES は東京リージョンでサンドボックス解除が未申請（DESIGN §8.4）。解除前は検証済みドメイン宛のみ・200 通/日に制限されるが、料金体系は同じである。
- AWS の料金は変更される可能性がある。金額は 2026-08-28 時点の単価に基づく。

---

## 9. 関連ドキュメント

| 種別 | パス | 用途 |
| --- | --- | --- |
| 上位設計書（基盤） | [`DESIGN.md`](DESIGN.md) | リソース構成・スタック分割・**入口の IP 制限（§7 CloudFront Function による IP 判定）**・通知基盤・ログ保持（§12.4）・コスト方針（§16 CloudFront 従量課金） |
| 上位設計書（アプリ） | [`apps/saas-a-billing/DESIGN.md`](apps/saas-a-billing/DESIGN.md) | バッチ経路・Lambda のメモリ / タイムアウト・実測値 |
| 技術調査 | [`research/research_R5.md`](research/research_R5.md) | 採用技術の裏取り |
| Excel 版 | 公開版では省略 | 同内容を Excel 表形式で（社内配布用） |

---

## 10. 付録: AWS Pricing MCP 取得 sku 一覧

| サービス | productFamily / group | usagetype | sku | リージョン |
| --- | --- | --- | --- | --- |
| **AmazonCloudFront** | **Request / CloudFront-Functions** | **Executions-CloudFrontFunctions** | **QAZUUEJG8GARJCET** | **Global** |
| AmazonCloudFront | Request | JP-Requests-Tier2-HTTPS | EJZT492VM8J2VQTQ | Global（Japan） |
| AmazonCloudFront | Data Transfer | JP-DataTransfer-Out-Bytes | CME9BRJSS2F5PBYE | Global（Japan） |
| CloudFrontPlans | Request（FlatRate） | Global-CloudFrontPlan-Free | 62NKXBU85SAM2UCH | Global |
| CloudFrontPlans | Request（FlatRate） | Global-CloudFrontPlan-Pro | ZV6NSURPZJGXK373 | Global |
| AmazonApiGateway | API Calls / ApiGatewayHttpApi | APN1-ApiGatewayHttpRequest | 5TU3JKA59EGYZ8XY | ap-northeast-1 |
| AWSLambda | Serverless / AWS-Lambda-Requests | APN1-Request | 3BE8DYKG4FYSZGDW | ap-northeast-1 |
| AWSLambda | Serverless / AWS-Lambda-Duration | APN1-Lambda-GB-Second | FSYUV9NMNDEXRJ5H | ap-northeast-1 |
| AmazonStates | AWS Step Functions / SFN-StateTransitions | APN1-StateTransition | BTKF2VQUME7BCFVV | ap-northeast-1 |
| AWSEvents | EventBridge / Invocation | APN1-ScheduledInvocation | 29C3758CBCYNXH8B | ap-northeast-1 |
| AmazonDynamoDB | PayPerRequest Throughput / DDB-WriteUnits | APN1-WriteRequestUnits | HBK75W4NW4DYPFAC | ap-northeast-1 |
| AmazonDynamoDB | PayPerRequest Throughput / DDB-ReadUnits | APN1-ReadRequestUnits | TX6BW4TWG2FZQMPQ | ap-northeast-1 |
| AmazonDynamoDB | Database Storage / Indexed DataStore | APN1-TimedStorage-ByteHrs | 2HPSAWPXJ2JJJ6XY | ap-northeast-1 |
| AmazonS3 | Storage / General Purpose | APN1-TimedStorage-ByteHrs | CCBFTMENTDWZHXHX | ap-northeast-1 |
| AmazonS3 | API Request / S3-API-Tier1 | APN1-Requests-Tier1 | XAKHUSC46NWC9PPM | ap-northeast-1 |
| AmazonS3 | API Request / S3-API-Tier2 | APN1-Requests-Tier2 | 9C7873KU3ZQBD6BJ | ap-northeast-1 |
| AWSQueueService | API Request / SQS-APIRequest-Tier1 | APN1-Requests-Tier1 | 5Q2SBURHPCGERHBD | ap-northeast-1 |
| AmazonSES | Sending Email / Send | APN1-Recipients | 824QF4XUVN48GVC9 | ap-northeast-1 |
| AmazonSES | Sending Attachments / Send | APN1-AttachmentsSize-Bytes | 76U4Q2ZC23BDQBWD | ap-northeast-1 |
| AmazonSNS | Message Delivery | APN1-DeliveryAttempts-SMTP | U7N84S2RCJJJKURJ | ap-northeast-1 |
| AmazonSNS | API Request / SNS-Requests-Tier1 | APN1-Requests-Tier1 | RFRANPTXHKXT5NFV | ap-northeast-1 |
| AmazonCognito | Amazon Cognito - Lite | APN1-CognitoLiteMAU | CNHN82F3S2AZPTXR | ap-northeast-1 |
| AmazonCloudWatch | Data Payload / Ingested Logs | APN1-DataProcessing-Bytes | CWB2GTZJXNX3TA6H | ap-northeast-1 |
| AmazonCloudWatch | Storage Snapshot / Standard Storage | APN1-TimedStorage-ByteHrs | 42CVQU4BPES8AWHC | ap-northeast-1 |
| AmazonCloudWatch | Alarm | APN1-CW:AlarmMonitorUsage | FBMB6TVC844YGZ3J | ap-northeast-1 |
| AmazonRoute53 | DNS Query | Intra-AWS-DNS-Queries（Alias は無料） | SJ2RJA9FC4AMHVM9 | Global |

### 参考: AWS WAF の sku（v0.3 で計上対象外）

WAF を後から追加する場合の試算（§8.2）に用いる。**本見積の合計には含めていない。**

| サービス | productFamily / group | usagetype | sku | 単価 | リージョン |
| --- | --- | --- | --- | ---: | --- |
| awswaf | Web Application Firewall / Web ACL | USE1-WebACLV2 | KF2QAJUBXF7XNYSM | $5.00/月 | us-east-1 |
| awswaf | Web Application Firewall / Rule | USE1-RuleV2 | JGH3VFMTQFNJUV5W | $1.00/月 | us-east-1 |
| awswaf | Web Application Firewall / Request | USE1-RequestV2-Tier1 | 94S3KGQ8C7XGQFSJ | $0.60/100 万 | us-east-1 |

### Web 取得で補った項目

AWS Pricing MCP は**単価**を返すが、常時無料枠の枠量を返さないものがある。以下は AWS 公式サイト（aws.amazon.com）から取得した（2026-08-28）。サードパーティ情報は使用していない。

| 項目 | 値 | 出典 URL |
| --- | --- | --- |
| Lambda 無料枠 | 100 万リクエスト + 400,000 GB-秒 / 月 | https://aws.amazon.com/lambda/pricing/ |
| SQS 無料枠 | 100 万リクエスト / 月（全顧客対象） | https://aws.amazon.com/sqs/pricing/ |
| Cognito Lite 無料枠 | 10,000 MAU / 月（無期限、2024-11-22 以降作成のプール） | https://aws.amazon.com/cognito/pricing/ |
| Step Functions 無料枠 | 4,000 状態遷移 / 月（無期限） | https://aws.amazon.com/step-functions/pricing/ |
| CloudWatch 無料枠 | アラーム 10 個、Logs 5 GB（取込 + アーカイブ保存 + Insights スキャン） | https://aws.amazon.com/cloudwatch/pricing/ |
| CloudFront 定額プランの内容 | Free $0（100 万リクエスト / 100 GB / S3 5 GB）、Pro $15 ほか | https://aws.amazon.com/cloudfront/pricing/ |

> **CloudFront Functions の 200 万実行無料枠は Web 取得ではなく MCP 取得**である。sku `QAZUUEJG8GARJCET` の価格ディメンションに「$0 for First 2,000,000 CloudFront Function Executions」（beginRange 0 / endRange 2,000,000）として定義されているため、Price List API の値をそのまま用いた。

---

## 11. 改訂履歴

| バージョン | 日付 | 改訂内容 | 改訂者 |
| --- | --- | --- | --- |
| v0.4 | 2026-09-02 | **CloudWatch アラームの個数を実装に合わせて 3 個 → 5 個 / 環境に是正**（core の notify DLQ 1 本 + saas-a-billing 4 本。DESIGN §8.1 / アプリ設計 §14.2）。固定費は $0.30 → **$0.50**/環境、無料枠適用前の月額合計は dev $0.92 → **$1.12**、prod $1.24 → **$1.44**、dev + prod $2.15 → **$2.55**（将来 prod $2.34 → $2.54）。常時無料枠は 10 アラームメトリクスで、dev 5 + prod 5 = **ちょうど 10 個**のため無料枠適用後は引き続き $0.00 だが余裕はゼロである旨を §4 と §6 に明記した。**無料枠適用後の月額（dev $0.08 / prod $0.41 / 合計 $0.49）は変わらない**。新規単価取得なし。Excel 版は未再生成。 | 開発部 |
| v0.3 | 2026-08-28 | **設計変更（ユーザー決定）を反映: AWS WAF を廃止し、社内 IP 制限を CloudFront Function（viewer-request）に置き換え**（DESIGN §7）。§2 から WAF WebACL / IPSet を削除しリソース ID を R1〜R21 に振り直し、§3 単価一覧と §5.2 従量費から WAF の 3 行（WebACL / ルール / リクエスト）を削除。CloudFront Functions の行を明示し、実行回数を**ディストリビューションの総リクエスト数と同数**（dev 20,000 / prod 100,000 / 将来 200,000）として計上、常時無料枠 200 万実行/月（MCP sku `QAZUUEJG8GARJCET` の $0 レンジ）内で **$0.00** とした。固定費は $6.30 → **$0.30**/環境（無料枠適用後 $0.00）。月額合計（無料枠適用後）は dev $6.09 → **$0.08**、prod $6.47 → **$0.41**、dev + prod $12.56 → **$0.49**（-96%、年 ¥23,364 → ¥914）、将来 dev + prod $13.05 → **$0.92**。§8.2 の「WAF を CloudFront Function に置換」は「採用済み」に書き換え、「WAF を後から付ける場合の追加費用（1 環境 約 $6.06/月）」として残した。WAF の sku は §10 に参考として保持。§9 の設計書参照を DESIGN §7（CloudFront Function による IP 制限）に更新。Excel 版も同日再生成。新規単価取得なし。 | 開発部 |
| v0.2 | 2026-08-28 | 設計書を正として 2 点を是正。(1) **WAF ルールを 2 本 → 1 本**（`DefaultAction: Block` は課金対象ルールではない）。固定費 $7.30 → $6.30/環境。(2) **CloudWatch Logs の保持期間を 90 日 → 365 日**（DESIGN §12.4）。ストレージ前提を 0.6 GB → 2.4 GB/環境（将来 1.5 GB → 6.0 GB）に更新した結果、dev + prod で常時無料枠 5 GB を 0.2 GB 超過（将来は 4.1 GB 超過）し、超過分を保存料金で計上。月額合計（無料枠適用後）は dev $6.09 / prod $6.47 / 合計 $12.56。あわせて CloudFront 定額プランの参照先を DESIGN §17 #1（旧・要確認事項）から §16 へ、SES サンドボックスの参照先を §17 #3 から §8.4 へ差し替えた。 | 開発部 |
| v0.1 | 2026-08-28 | 初版作成。AWS Pricing MCP（Price List API）で ap-northeast-1 / us-east-1（WAF）/ Global（CloudFront・Route 53）の単価をすべて取得。`aws-cost-estimate` スキル準拠フォーマット。dev / prod / 将来（3,000 地点）の 3 シナリオで試算。Excel 版併設（`files/料金見積.xlsx`）。 | 開発部 |
