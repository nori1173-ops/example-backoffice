# SaaS_A 課金レポート（`saas-a-billing`）

SaaS_A の月額ライセンス料金を月次で集計し、Excel をメール添付 + Slack で配布する
example-backoffice のアプリスタック。設計は
[アプリ設計書](../../Documents/apps/saas-a-billing/DESIGN.md)、全体像は
[プラットフォーム設計書](../../Documents/DESIGN.md)、追加手順は
[アプリ追加手順](../../Documents/ADD-APP-GUIDE.md) を参照する。

## 構成

| パス | 内容 |
| --- | --- |
| `template.yaml` | HTTP API / 6 関数 / ステートマシン / DynamoDB / DLQ 2 本 / アラーム 4 本 / LogGroup 8 本 / SSM `/apps/saas-a-billing/api-domain` |
| `samconfig.toml` | `[default]`（dev）と `[production]`。prod は SSM の**キー名**を上書きする |
| `functions/` | 6 関数が共有する 1 パッケージ（`CodeUri` は 1 つ、Handler だけを分ける） |
| `statemachine/run.asl.json` | `saas-a-billing-run` の定義。関数 ARN は `DefinitionSubstitutions` で注入する |
| `tests/` | pytest（`unit` / `integration`）。テンプレートの検査は `tests/infra/` 側 |
| `edge-snippet.yaml` | `infra/edge/template.yaml` へ転記済みの origin / behavior / パラメータの写し |

| 関数 | Handler | timeout / メモリ | 役割 |
| --- | --- | --- | --- |
| `ApiFunction` | `api.app.handler` | 29 秒 / 512MB | `ANY /api/saas-a-billing/{proxy+}` |
| `TriggerFunction` | `batch.handlers.trigger.handler` | 60 秒 / 256MB | 毎日 06:00 JST に実行要否を判定 |
| `InitFunction` | `batch.handlers.init.handler` | 900 秒 / 1024MB | RUN 取得・設定/単価解決・`getToken`・`tenant/list` |
| `CollectTenantFunction` | `batch.handlers.collect_tenant.handler` | 900 秒 / 1024MB | テナント 1 件の収集 → `parts/{tenant_cd}.json` |
| `AggregateFunction` | `batch.handlers.aggregate.handler` | 900 秒 / 1024MB | 集約・Excel 生成・S3 保存・通知 |
| `MarkFailedFunction` | `batch.handlers.mark_failed.handler` | 900 秒 / 1024MB | RUN=`failed` + 失敗通知 |

## テスト

アプリ直下で実行する。どのアプリも `tests/conftest.py` と `functions/` を同じ名前で持つため、
リポジトリルートから複数アプリをまとめて収集すると import が衝突する。

```bash
cd apps/saas-a-billing
uv run pytest tests -v
sam validate --lint
```

テンプレート・samconfig・edge-snippet の静的検査はリポジトリルートで実行する。

```bash
uv run pytest tests/infra -v
```

## デプロイ

`sam build` を必ず先に実行する（`requirements.txt` の依存 XlsxWriter / requests を含めるため）。

```bash
cd apps/saas-a-billing
sam build --use-container && sam deploy                          # dev
sam build --use-container && sam deploy --config-env production  # prod
```

デプロイ順は shared → core → app → edge（プラットフォーム設計 3.3）。デプロイすると
`/example-backoffice/{env}/apps/saas-a-billing/api-domain` が SSM に書かれ、
`scripts/deploy-edge.sh {env}` がそれを読んで CloudFront の origin にする。

デプロイ前に集計対象環境の接続情報を SecureString で登録しておく（設計 14.1）。

```bash
for LEAF in base-url user-id password; do
  aws ssm put-parameter --type SecureString --overwrite \
    --name "/example-backoffice/dev/apps/saas-a-billing/targets/verification/$LEAF" --value "<値>"
done
```

## 疎通確認

```bash
SECRET=$(aws ssm get-parameter --name /example-backoffice/dev/edge/origin-verify-secret --with-decryption --query Parameter.Value --output text)
API=$(aws ssm get-parameter --name /example-backoffice/dev/apps/saas-a-billing/api-domain --query Parameter.Value --output text)
curl -s "https://$API/api/saas-a-billing/settings" -H "Authorization: Bearer $TOKEN" -H "x-origin-verify: $SECRET"
```

CloudFront 経由は `https://backoffice-dev.example-cloud.com/api/saas-a-billing/settings`。

## 監視（設計 14.2）

| アラーム | 監視対象 |
| --- | --- |
| `StateMachineFailedAlarm` | `ExecutionsFailed + ExecutionsTimedOut`（1 日）|
| `LambdaErrorsAlarm` | 自スタック 6 関数の `Errors` の合算（1 日）|
| `SchedulerDlqAlarm` / `TriggerFailureAlarm` | 各 SQS の `ApproximateNumberOfMessagesVisible` |

- 業務上の失敗は `MarkFailed`（`End: true`）が受け止めるため `ExecutionsFailed` には計上されない。`StateMachineFailedAlarm` が拾うのは実行タイムアウト・`States.Runtime`・`MarkFailed` 自体の失敗で、業務失敗の通知は Slack / メール（設計 6.8）と実行履歴で見る
- Scheduler ロールの Scheduler DLQ への `sqs:SendMessage` は SAM が `ScheduleV2` の `DeadLetterConfig.Arn` から自動生成するため、テンプレートに明示していない
- アラームの通知先（SNS）は core の `OpsTopic` に SSM 契約が無いため未接続。CloudWatch コンソールで状態を見る
