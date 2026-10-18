# UR賃貸 空室ウォッチャー（新浦安エリア）

新浦安エリアのUR賃貸住宅の空室を1時間おきにチェックし、**新しく出た空室**をSlackに通知します。

- 監視している団地: 浦安市内のUR団地6か所（`config.yaml`）
- 条件: 家賃＋共益費が200,000円以下（面積・間取り・階数の条件も `config.yaml` で追加できます）
- 実行場所: GitHub Actions（毎時17分ごろ。手動でも実行できます）
- データの取得元: URのサイトが内部で使っているJSON API（[docs/data-source.md](docs/data-source.md)）

## 通知のされ方

| タイミング | Slackに届くもの |
|---|---|
| 初回の実行 | 🏠 現在の空室一覧（全件。0件でも「監視を始めました」と届きます） |
| 新しい空室が出た | 🆕 新着の空室（1部屋ずつ、間取り・面積・階数・家賃・共益費・合計・最寄り駅・部屋ページへのリンク）。前回から埋まった部屋があれば末尾に小さく添えます |
| 変化がない／埋まっただけ | 何も届きません |
| 同じ団地の取得に3回連続で失敗した | ⚠️ エラー通知（復旧するまでは1回だけ） |

例:

```
🆕 新浦安エリアに新着の空室 1件
条件: 家賃+共益費 ≤ ¥200,000 | 2026-10-16 17:00 時点
──────────────
🆕 浦安マリナイースト２１望海の街　11号棟2608号室   [部屋を見る]
1LDK ／ 54㎡ ／ 26階（28階建）
¥164,300 + ¥7,100 = ¥171,400
🚉 JR京葉線「新浦安」駅 徒歩22～26分 又はバス7～10分 徒歩1～4分
──────────────
埋まった空室（1件）: 浦安マリナイースト２１海園の街　5号棟204号室
```

部屋名と「部屋を見る」ボタンは、URの部屋ページにリンクしています。

## セットアップ

### 1. Slack の Incoming Webhook を作る

1. https://api.slack.com/apps を開いて **Create New App** → **From scratch** を選ぶ
2. App Name に `UR空室ウォッチャー` などと入れ、通知したいワークスペースを選んで **Create App**
3. 左のメニューの **Incoming Webhooks** を開き、**Activate Incoming Webhooks** をオンにする
4. 下の **Add New Webhook to Workspace** を押し、通知を流したいチャンネルを選んで **許可する**
5. `https://hooks.slack.com/services/...` で始まる **Webhook URL** をコピーする
   （このURLを知っている人は誰でもそのチャンネルに投稿できるので、他人に見せたりコードに書いたりしないでください）

### 2. GitHub の Secrets に Webhook URL を登録する

1. GitHub でこのリポジトリを開き、**Settings** → **Secrets and variables** → **Actions** を開く
2. **New repository secret** を押す
3. Name に `SLACK_WEBHOOK_URL`、Secret に手順1でコピーしたURLを貼り付けて **Add secret**

### 3. Actions を動かす

1. ワークフロー（`.github/workflows/watch.yml`）を **デフォルトブランチ（main）に入れる**
   （スケジュール実行はデフォルトブランチにあるワークフローだけが対象です）
2. **Settings** → **Actions** → **General** → **Workflow permissions** が
   「Read and write permissions」になっているか確認する（`state/vacancies.json` をcommitするため）
3. **Actions** タブ → **UR vacancy watch** → **Run workflow** で手動実行する
4. Slack に「🏠 新浦安エリアの現在の空室 N件」が届けば完了です。以降は毎時17分ごろに自動で動きます
   （GitHub側の混雑で数分〜十数分遅れることがあります）

> **注意:** GitHub は、リポジトリに60日間コミットなどの動きがないとスケジュール実行を自動で止めます。
> 空室の変化があれば state のcommitで動きが出ますが、止まった場合は Actions タブから再度有効にしてください。

## 条件や団地を変える

`config.yaml` を編集してcommitします。

```yaml
filters:
  max_total_rent: 200000   # 家賃＋共益費の上限（必須）
  min_area: 50             # 50㎡以上
  min_layout: "1DK"        # 1DK以上（1K < 1DK < 1LDK < 2K < 2DK < 2LDK < ...）
  min_floor: 3             # 3階以上
```

団地を追加するときは、団地ページのURL（例: `https://www.ur-net.go.jp/chintai/kanto/chiba/30_5120.html`）から
IDを `30_512_0` の形で書きます（`30` / `512` / 末尾の `0`）。

条件を変えると、新しく条件に合うようになった部屋は次回「新着」として通知されます。

## ローカルで動かす

```bash
pip install -r requirements.txt -r requirements-dev.txt

# Slack に送らず、通知の内容を表示する（state も更新しません）
python -m src.main --dry-run

# Block Kit の JSON も見たいとき
python -m src.main --dry-run --json

# テスト（URにはアクセスしません）
pytest
```

## ファイル構成

```
.github/workflows/watch.yml  毎時の実行と state のcommit
src/fetch.py                 URのAPIから団地ごとに空室を取得（リトライ・ページ送り）
src/filter.py                config.yaml の条件で絞り込み
src/diff.py                  前回との差分（新着／埋まった）
src/notify.py                Slack の Block Kit メッセージの作成と送信
src/main.py                  全体の流れ・--dry-run
config.yaml                  監視する団地と条件
state/vacancies.json         前回の空室一覧（Actionsが自動で更新）
docs/data-source.md          URのAPIの調査メモ
tests/                       テスト
```

## 安定性のための工夫

- リクエストの間に1.5秒空け、User-Agent にツール名を明示しています（1時間に6〜7回程度のアクセス）
- 取得に失敗したら、2・4・8秒と間隔を延ばしながら最大3回リトライします
- それでも取れなかった団地は **前回の空室をそのまま残す** ので、「全部埋まった」と誤って判定しません
- Slack への送信に失敗したら state を保存しないので、次の回にもう一度通知されます
