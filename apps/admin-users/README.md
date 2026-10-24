# ユーザー管理（`admin-users`）

example-backoffice のアプリスタック。全体像は [プラットフォーム設計書](../../Documents/DESIGN.md)、
アプリの設計は [ユーザー管理 設計書](../../Documents/apps/admin-users/DESIGN.md)、
追加手順は [アプリ追加手順](../../Documents/ADD-APP-GUIDE.md) を参照する。

`admin` グループの管理者が、サインアップ済みユーザーの一覧・admin 付与/解除・
無効化/有効化・パスワードリセット・削除を画面から行うための API を提供する。

## 構成

| パス | 内容 |
| --- | --- |
| `template.yaml` | HTTP API / api Lambda / LogGroup / SSM `/apps/admin-users/api-domain` |
| `samconfig.toml` | `[default]`（dev）と `[production]`。prod は SSM の**キー名**を上書きする |
| `functions/admin_users/app.py` | HTTP API のルート（`Handler: admin_users.app.handler`） |
| `functions/admin_users/cognito.py` | Cognito 管理 API の薄いラッパ（ページング・`sub` 検索） |
| `tests/` | pytest + moto（`cognitoidp`）。`conftest.py` が `functions/` を `sys.path` に足す |
| `edge-snippet.yaml` | `infra/edge/template.yaml` へ転記済みの origin / behavior / パラメータの写し |

DynamoDB テーブル・バッチ・ステートマシン・通知は持たない（雛形から削除済み）。
操作の証跡は Lambda の構造化ログ（CloudWatch Logs、保持 365 日）に残す。

**ハンドラのモジュール名は雛形の `api.app` ではなく `admin_users.app`。** 全アプリの
テストを 1 つの pytest セッションで走らせると `api` という同名パッケージが sys.modules で
衝突し、先に読まれた別アプリのモジュールを掴むため（ADD-APP-GUIDE 2 章ステップ 4-7）。

## テスト

```bash
cd apps/admin-users
uv run pytest tests -v
uv run pytest tests --cov=functions --cov-report=term-missing --cov-fail-under=80
sam validate --lint
```

リポジトリ全体は `uv run pytest`（`pyproject.toml` の `pythonpath` に
`apps/admin-users/functions` を登録済み）。

## デプロイ

`sam build` を必ず先に実行する。

```bash
cd apps/admin-users
sam build --use-container && sam deploy                          # dev
sam build --use-container && sam deploy --config-env production  # prod
```

デプロイ順は shared → core → app → edge（設計 3.3）。デプロイすると
`/example-backoffice/{env}/apps/admin-users/api-domain` が SSM に書かれる。
edge 側（origin / behavior）と `scripts/deploy-edge.sh` は追記済みなので、
アプリのデプロイ後に `scripts/deploy-edge.sh {env}` を流せば経路が繋がる。

## 疎通確認

```bash
SECRET=$(aws ssm get-parameter --name /example-backoffice/dev/edge/origin-verify-secret --with-decryption --query Parameter.Value --output text)
API=$(aws ssm get-parameter --name /example-backoffice/dev/apps/admin-users/api-domain --query Parameter.Value --output text)
curl -s "https://$API/api/admin-users/users" -H "Authorization: Bearer $TOKEN" -H "x-origin-verify: $SECRET"
```

`TOKEN` は `admin` グループのユーザーの ID トークン（`uv run python scripts/get_id_token.py dev {email}`）。
全ルートが `admin` 専用なので、一般ユーザーのトークンでは 403 が返る。
CloudFront 経由の確認は `https://backoffice-dev.example-cloud.com/api/admin-users/users`。

## 運用

- 最初の管理者だけは画面から作れない。`scripts/add-admin.sh {env} {email}` で付ける
  （画面が使えないときの退路でもある）。
- パスワードリセットは本人にリセットコードをメールするだけで、新しいパスワードは
  本人がサインイン画面の「パスワードを忘れた場合」から設定する。
- 自分自身の admin 解除・無効化・削除はサーバが 403 で断る（画面のボタンも disabled）。
