# {{cookiecutter.app_title}}（`{{cookiecutter.app_id}}`）

example-backoffice のアプリスタック。全体像は [プラットフォーム設計書](../../Documents/DESIGN.md)、
追加手順は [アプリ追加手順](../../Documents/ADD-APP-GUIDE.md) を参照する。

## 構成

| パス | 内容 |
| --- | --- |
| `template.yaml` | HTTP API / api Lambda / DynamoDB / LogGroup / SSM `/apps/{{cookiecutter.app_id}}/api-domain` |
| `samconfig.toml` | `[default]`（dev）と `[production]`。prod は SSM の**キー名**を上書きする |
| `functions/` | Lambda の `CodeUri`。`api/app.py`（HTTP API）と `batch/handlers/`（バッチ） |
| `statemachine/run.asl.json` | Step Functions を使う場合の定義（既定は未使用） |
| `tests/` | pytest。`conftest.py` が `functions/` を `sys.path` に足す |
| `edge-snippet.yaml` | `infra/edge/template.yaml` に転記する origin / behavior / パラメータ |

## テスト

アプリ直下で実行する。どのアプリも `tests/conftest.py` と `functions/` を同じ名前で持つため、
リポジトリルートから複数アプリをまとめて収集すると import が衝突する。

```bash
cd apps/{{cookiecutter.app_id}}
uv run pytest tests -v
uv run pytest tests --cov=functions --cov-report=term-missing --cov-fail-under=80
sam validate --lint
```

## デプロイ

`sam build` を必ず先に実行する（`requirements.txt` の依存を含めるため）。

```bash
cd apps/{{cookiecutter.app_id}}
sam build --use-container && sam deploy                          # dev
sam build --use-container && sam deploy --config-env production  # prod
```

デプロイ順は shared → core → app → edge（設計 3.3）。デプロイすると
`/example-backoffice/{env}/apps/{{cookiecutter.app_id}}/api-domain` が SSM に書かれる。

## 疎通確認

```bash
SECRET=$(aws ssm get-parameter --name /example-backoffice/dev/edge/origin-verify-secret --with-decryption --query Parameter.Value --output text)
API=$(aws ssm get-parameter --name /example-backoffice/dev/apps/{{cookiecutter.app_id}}/api-domain --query Parameter.Value --output text)
curl -s "https://$API/api/{{cookiecutter.app_id}}/health" -H "Authorization: Bearer $TOKEN" -H "x-origin-verify: $SECRET"
```

CloudFront 経由の確認は `edge-snippet.yaml` を `infra/edge/template.yaml` へ転記し、
`scripts/deploy-edge.sh dev` を実行してから `https://backoffice-dev.example-cloud.com/api/{{cookiecutter.app_id}}/health` を叩く。

## 残作業

- [ ] `functions/api/app.py` に業務ルートを追加する（設定変更は `auth.require_group(claims, "admin")` で保護）
- [ ] `functions/batch/` を使うなら `template.yaml` の `ScheduleV2` ブロックのコメントを外す
- [ ] `frontend/src/apps/_template/` を `frontend/src/apps/{{cookiecutter.app_id}}/` にコピーして書き換える
- [ ] `Documents/apps/{{cookiecutter.app_id}}/DESIGN.md` を書く
- [ ] リポジトリルート `README.md` のアプリ一覧に追記する
