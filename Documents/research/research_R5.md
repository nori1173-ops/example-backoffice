# 調査報告 R5（2026-08-28 時点）

## 1. CloudFront 定額プラン（Free）

**結論**
- (a) 前提が逆。「AWS Free Tier 利用中」は**適格条件ではなく除外条件**。公式に「AWS accounts are not eligible for pricing plans if ... **Your account is using AWS Free Tier**」。よって Free Tier を終了した既存法人アカウントは選択可（Free プランは 1 アカウント 3 個まで、プラン全体 100 個、1 プランあたり apex ドメイン 1 個）。過去の利用実績が上位ティア相当だと下位ティアを選べない場合あり。Shield Advanced 有効、Firewall Manager 管理の WebACL は不可。
- (b) **CloudFormation/SAM からは指定不可（未対応）**。CFN リソース型リファレンスに `AWS::CloudFront::PricingPlan` も `AWS::PricingPlanManager::*` も存在しない。管理は Console / AWS CLI (`aws pricing-plan-manager`) / PricingPlanManager API（CreateSubscription, ApprovePaidSubscription, AssociateResourcesToSubscription 等）。
- (c) Free は **WAF ルール 5 個**（「custom rules と AWS Managed Rules の合計」）。プラン利用時は **Rule Group 使用不可**（個別ルールで書く）、WebACL の関連付けは**必須**かつ他ディストリビューションと共有不可。**IP set ベースのルールが Free で使えるか・5 枠に含まれるかは公式に明記なし（未確認）**。カスタムルールの一種なので 1 枠消費と読むのが自然（推測）。機能表に IP 許可/拒否リストの行は無く、Free にあるのは「IP-based rate limiting」「Geographic traffic blocking」まで。
- (d) Free の月間枠: **リクエスト 1M / 転送 100GB / S3 5GB クレジット**。ハード上限ではなく超過課金なし。WAF でブロックした分は枠に計上されない。Route 53 は ALIAS 無制限＋その他 1M クエリ。

**根拠**
- https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/flat-rate-pricing-plan.html （Account-level constraints / Unsupported features / Monthly usage allowances / Flat-rate pricing plan quotas）
- https://aws.amazon.com/cloudfront/pricing/ （Free $0, 1M req, 100GB, 5 WAF rules）
- https://docs.aws.amazon.com/PricingPlanManager/latest/UserGuide/plan-management.html
- https://docs.aws.amazon.com/waf/latest/developerguide/cloudfront-features.html （WebACL 必須・ルールは自由に調整可）
- https://github.com/hashicorp/terraform-provider-aws/issues/49232 （"CloudFormation support is not yet available"、Go SDK pricingplanmanager v1.0.0 は 2026-07-30）
- CFN テンプレートリファレンス TOC に該当リソース無し（toc-AWS_CloudFront.json を実確認）

**設計への影響**: SAM 一発では組めない。ディストリビューションを SAM で作り、プラン加入だけ CLI/API の後処理（デプロイスクリプト or カスタムリソース）にする必要がある。Free は**アクセスログ・WAF ログが一切出ず、カスタム WAF レスポンスも不可**なので、社内保守サイトの監査ログ要件があるなら Pro($15) 以上か従量課金を検討。

## 2. Cognito 機能プラン（Lite / Essentials / Plus）

**結論**
- TOTP（SOFTWARE_TOKEN_MFA）は **Lite で使える**（機能表「MFA with authenticator apps and SMS one-time codes」= Lite + Essentials + Plus）。
- Lambda トリガ（Pre sign-up 含む）も **Lite で使える**（「Custom runtime actions with Lambda triggers」= 全プラン）。ただし ID トークン以外の**アクセストークンのカスタマイズ**は Essentials 以上。
- WAF WebACL 関連付けは**プラン非依存**（公式ページに feature plan 要件の記載なし＝全プランで可）。ただし WAF 分は別課金、ATP/ACFP マネージドルールグループとの併用不可、**CAPTCHA アクションは managed login / classic hosted UI の TOTP 登録を壊す**（`x-amzn-cognito-operation-name` が AssociateSoftwareToken / VerifySoftwareToken のものを CAPTCHA から除外する必要あり）。
- CloudFormation: `AWS::Cognito::UserPool` の `UserPoolTier`、許容値 **`LITE | ESSENTIALS | PLUS`**、既定 `ESSENTIALS`。`AdvancedSecurityMode` を AUDIT/ENFORCED にすると PLUS 必須。
- 無料枠 10,000 MAU は **Lite と Essentials のみ**適用、**Plus には無料枠なし**。

**根拠**
- https://docs.aws.amazon.com/cognito/latest/developerguide/cognito-sign-in-feature-plans.html
- https://docs.aws.amazon.com/cognito/latest/developerguide/user-pool-waf.html
- https://docs.aws.amazon.com/cognito/latest/developerguide/user-pool-settings-mfa-totp.html （§Configuring your AWS WAF web ACL for managed login TOTP MFA）
- https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-cognito-userpool.html
- https://aws.amazon.com/cognito/pricing/

**設計への影響**: `UserPoolTier: LITE` で TOTP + Pre sign-up + WAF が成立し、10,000 MAU まで無料。ただし Lite は **managed login（新 UI）が使えず classic hosted UI のみ**なので、ログイン画面の見栄えを取るなら ESSENTIALS。

## 3. boto3 / SES v2 添付メール

**結論**
- `SendEmail` の `Content.Simple.Attachments` 対応は **boto3 1.37.27 / botocore 1.37.27（2025-04-03 リリース）が最小**。それ未満では ParamValidationError になる。
- `Attachment` の必須は `FileName`（最大 255 文字）と `RawContent`（SDK 側が base64 化）。任意で `ContentType` / `ContentDisposition`(ATTACHMENT|INLINE) / `ContentId` / `ContentTransferEncoding` / `ContentDescription`。
- Lambda Python 3.13 の同梱 boto3/botocore バージョンは、現在の AWS 公式ドキュメントが**バージョン表を廃止**し「ランタイムとリージョンに依存するので関数内で print して確認せよ」としているため、**具体値は未確認**。

**根拠**
- botocore CHANGELOG 1.37.27: 「sesv2: This release enables customers to provide attachments in the SESv2 SendEmail and SendBulkEmail APIs.」https://github.com/boto/botocore/blob/develop/CHANGELOG.rst
- PyPI 実測: botocore 1.37.27 / boto3 1.37.27 とも 2025-04-03 アップロード（boto3 1.37.27 は `botocore>=1.37.27,<1.38.0`）
- https://aws.amazon.com/about-aws/whats-new/2025/04/amazon-ses-attachments-sending-apis （2025-04-04 発表）
- https://docs.aws.amazon.com/ses/latest/APIReference-V2/API_Attachment.html
- https://docs.aws.amazon.com/lambda/latest/dg/lambda-python.html §Runtime-included SDK versions

**設計への影響**: 同梱版に賭けない。`requirements.txt` に `boto3>=1.37.27` を明記して固定する（リージョン差でランタイム同梱が古い可能性を排除できないため）。SES 添付の非対応拡張子リスト（mime-types）も要確認。

## 4. CloudFormation `Fn::GetStackOutput`

**結論**
- **SAM テンプレート内で使える（実測済み）**。SAM CLI 1.154.0 で `sam validate` / `sam build` を実行し、`Fn::GetStackOutput` はビルド後テンプレートへ**そのまま透過**された（SAM transform は解釈も破壊もしない）。aws-sam-cli / serverless-application-model リポジトリに `GetStackOutput` の言及は 0 件＝特別扱いなし。
- cfn-lint は **1.51.0 で対応**（2026-05-12）、**1.51.2 で Outputs / Fn::Sub / Fn::Base64 内も許容**（2026-05-27）。それ以前は `E3012 ... is not of type 'string'` で誤検知する（ローカル cfn-lint 1.43.4 で再現確認）。最新 aws-sam-cli 1.165.0 は `cfn-lint>=1.51.3,<1.53` を固定するので `sam validate --lint` はOK。
- CFN 本体の既知制限: `Fn::Sub` の変数マップ値、`Fn::Base64` 内、`Outputs` 直下、`Conditions` の `Fn::Equals` 内、`Fn::ImportValue` 内は**未サポート（InternalFailure）**。使えるのはリソースプロパティ直下 / `Fn::Join` / `Fn::If` / `Fn::Select` 内のみ。クロスパーティション不可、弱参照（削除保護されない）。

**根拠**
- https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/intrinsic-function-reference-getstackoutput.html
- cfn-lint CHANGELOG v1.51.0 / v1.51.2 https://github.com/aws-cloudformation/cfn-lint/blob/main/CHANGELOG.md （PR #4495, #4519）
- 実測: SAM CLI 1.154.0 で build 後テンプレートに `Fn::GetStackOutput` が残存

**設計への影響**: 同一アカウント・同一リージョンなら素直に使える。ただし cfn-lint が古い CI では誤検知するので `cfn-lint>=1.51.3` を要求すること。Outputs へそのまま流せない点に注意。回避策が要る場合は従来どおり SSM パラメータ（`AWS::SSM::Parameter` + `Type: AWS::SSM::Parameter::Value<String>`）か `Fn::ImportValue`。

## 5. Slack 通知方式

**結論**
- (a) Incoming Webhook（Slack App 版）は**投稿先チャンネルを API 側で変更できない**。公式に「You **cannot override** the default channel (chosen by the user who installed your app), username, or icon when you're using incoming webhooks」。URL は「単一ユーザー・単一チャンネル」に紐づく。ペイロードの `channel` フィールドは無視される（旧 custom integration の webhook のみ上書き可だったが、docs.slack.dev の現行ページには legacy との対比記述なし＝未確認）。
- (b) `chat.postMessage` は Bot token の **`chat:write`** が必須。`channel` は「encoded ID または channel name」を受け付けるが、公式は「always use channel-like IDs」を推奨。**Bot が参加していない公開チャンネルへ投稿するには `chat:write.public` が追加で必要**（無いと `not_in_channel` / `no_permission`）。**非公開チャンネルは `chat:write.public` では不可**で、Bot を招待（メンバー）した上で**チャンネル ID 指定**が必要。`#` 付き `#name` 表記が受理されるかは公式に明記なし（未確認）。
- (c) メンション記法: `<!here>` / `<!channel>` / `<!everyone>`、ユーザー `<@U012AB3CD>`、ユーザーグループ `<!subteam^SAZ94GDB8>`、チャンネルリンク `<#C123ABC456>`。
- (d) ファイル添付（xlsx）は **`files.getUploadURLExternal` → 返却 URL へ HTTP POST → `files.completeUploadExternal`** の 3 ステップ。スコープは **`files:write`**（bot / user とも）。チャンネル共有は `completeUploadExternal` の **`channel_id`（ID のみ、名前不可）**、省略するとプライベート扱い。`initial_comment` / `thread_ts` 併用可。旧 `files.upload` は **2025-11-12 に廃止済み**。Python SDK の `files_upload_v2` が 3 ステップをラップする。

**根拠**
- https://docs.slack.dev/messaging/sending-messages-using-incoming-webhooks
- https://docs.slack.dev/reference/methods/chat.postMessage
- https://docs.slack.dev/messaging/formatting-message-text
- https://docs.slack.dev/messaging/working-with-files
- https://docs.slack.dev/reference/methods/files.completeUploadExternal
- https://docs.slack.dev/changelog/2024-04-a-better-way-to-upload-files-is-here-to-stay （files.upload sunset 2025-11-12）

**設計への影響**: 投稿先を環境（検証/本番）や通知種別で切り替えたい・xlsx を添付したい要件があるなら **Webhook ではなく Bot token 一択**。スコープは `chat:write` + `chat:write.public` + `files:write`。チャンネルは名前ではなく **ID を SSM/環境変数で持つ**設計にし、ファイル共有も `channel_id` で行う。
