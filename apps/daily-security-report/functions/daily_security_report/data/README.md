# IP 範囲表

生成日: 2026-10-15（UTC）

IPv4 / IPv6 の閉区間を整数の16進表記で保存。隣接する同一国・リージョンを結合し、gzip で圧縮する。
RIR の国コードは登録国であり所在地ではない。割当・割振り済みの範囲のみを使用する。
重複は最小範囲を優先し、同じ範囲では GLOBAL よりリージョンを優先する。

## 出典

- https://ip-ranges.amazonaws.com/ip-ranges.json
  SHA-256: 57bf0c55bdbd33a5f64809b4382708b30a23a5a4c3d2ef7d1d310e79502a83e0
- https://ftp.apnic.net/stats/apnic/delegated-apnic-extended-latest
  SHA-256: ec3c1cc83e60a4d344a337128835cf88c6975a5dde1dce3af4cdf1de31016f87
- https://ftp.arin.net/pub/stats/arin/delegated-arin-extended-latest
  SHA-256: 0d89f2fed48df1ae87954f97dfc0475090a9f2c048c10f8f66690588faeef02a
- https://ftp.ripe.net/pub/stats/ripencc/delegated-ripencc-extended-latest
  SHA-256: be88470e6dd67b864548795cf8fa35dd5ccfe7a794c874262f7784d88a8ce3b4
- https://ftp.lacnic.net/pub/stats/lacnic/delegated-lacnic-extended-latest
  SHA-256: 2e1c66a094bd98635fa2f97b27f35a0b9f0db1ae1ab71cce9c72ab73e0027fba
- https://ftp.afrinic.net/pub/stats/afrinic/delegated-afrinic-extended-latest
  SHA-256: 4c58753ff3390859a29647a0774c0787c8a6fc62910b29f6719d6684d9c7243b

## 更新

月 1 回を目安に出典からデータを取得し、リポジトリ外の入力ディレクトリに保存する。
次をリポジトリのルートで実行し、unit テストと差分を確認してから再デプロイする。

```bash
uv run python apps/daily-security-report/tools/build_ip_tables.py <入力ディレクトリ> apps/daily-security-report/functions/daily_security_report/data
uv run pytest apps/daily-security-report/tests/unit/test_ip_country.py -q
```

入力ディレクトリは必須引数。実行時の外部アクセスは行わず、tools/ は Lambda の CodeUri に含めない。
