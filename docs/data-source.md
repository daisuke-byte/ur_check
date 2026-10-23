# データ取得元の調査結果

調査日: 2026-10-16（実際にリクエストを送って確認済み）

## 結論

URの物件ページは、ブラウザ側のJavaScript（jQuery）が **`https://chintai.r6.ur-net.go.jp/chintai/api/`** 配下のJSON APIを叩いて描画している。
HTMLスクレイピングは不要で、次の2つのAPIで空室監視ができる。

| 用途 | エンドポイント | 使う場面 |
|---|---|---|
| ① 団地の空室一覧（本命） | `POST /chintai/api/bukken/detail/detail_bukken_room/` | 団地ごとに全空室を取得する（毎時の監視で使う） |
| ② エリア内の団地一覧 | `POST /chintai/api/bukken/result/bukken_result/` | 浦安市内の団地IDの洗い出し（config.yaml作成時に使用） |

根拠となるJSファイル：
- `https://www.ur-net.go.jp/chintai/common/js/api.js` … `ur.api.conf.url = 'https://chintai.r6.ur-net.go.jp/chintai/api/'`、`$.ajax({type:'post', dataType:'json'})`
- `https://www.ur-net.go.jp/chintai/common/js/api_bukken_detail.js` … `getDataRoom()` → `bukken/detail/detail_bukken_room/`
- `https://www.ur-net.go.jp/chintai/common/js/api_bukken_result.js` … `getDataRoom()` → `bukken/result/bukken_result/`

## 団地の識別子

団地は **`shisya`（支社）・`danchi`（団地番号）・`shikibetu`（識別）** の3つ組で識別される。
団地ページのURLにもそのまま現れる：

```
https://www.ur-net.go.jp/chintai/kanto/chiba/30_5120.html
                                              ^^ ^^^^
                                       shisya=30 danchi=512 + shikibetu=0
```

団地ページのHTML内でも `ur.api.bukken_detail.initSearch('30', '512', '0');` と呼ばれている。

## ① 団地の空室一覧 `detail_bukken_room`

### リクエスト

```
POST https://chintai.r6.ur-net.go.jp/chintai/api/bukken/detail/detail_bukken_room/
Content-Type: application/x-www-form-urlencoded

shisya=30&danchi=512&shikibetu=0&orderByField=0&orderBySort=0&pageIndex=0&sp=
```

| パラメータ | 値 | 説明 |
|---|---|---|
| `shisya` | `30` | 支社コード（千葉は30、東京は20） |
| `danchi` | `512` | 団地番号 |
| `shikibetu` | `0` | 識別コード |
| `orderByField` / `orderBySort` | `0` | 並び順（既定） |
| `pageIndex` | `0`, `1`, … | **0始まりのページ番号。1ページ5件** |
| `sp` | 空 | 空ならPC用、`sp` ならスマホ用 |

認証、Cookie、CSRFトークンは不要。

### レスポンス

- 空室あり … 部屋オブジェクトの **配列**（1ページ最大5件）
- 空室なし … **`null`**（本文は文字列 `null`）

```json
[
  {
    "pageIndex": "0",
    "rowMax": "5",
    "allCount": "1",
    "shisya": "30", "danchi": "512", "shikibetu": "0",
    "id": "000112608",
    "name": "11号棟2608号室",
    "type": "1LDK",
    "floorspace": "54&#13217;",
    "floor": "26階",
    "floorAll": "28階",
    "rent": "164,300円",
    "rent_normal": "",
    "commonfee": "7,100円",
    "shikikin": "2か月",
    "roomDetailLink": "/chintai/kanto/chiba/30_5120_room.html?JKSS=000112608",
    "madori": "https://chintai.r6.ur-net.go.jp/chintai/img_madori/...gif",
    "system": [{"制度名": "近居割", ...}]
  }
]
```

| フィールド | 例 | 使い道・パース方法 |
|---|---|---|
| `id` | `"000112608"` | **部屋ID**（`JKSS`の値。号棟と部屋番号から作られている）。一意キーは `shisya_danchi_shikibetu:id` にする |
| `name` | `"11号棟2608号室"` | 部屋番号の表示 |
| `type` | `"1LDK"` | 間取り |
| `floorspace` | `"54&#13217;"` | 面積。`&#13217;` は「㎡」の文字参照なので、数値部分を取り出す |
| `floor` | `"26階"` | 階数。数値部分を取り出す |
| `rent` | `"164,300円"` | 家賃（カンマと「円」を取り除く）。`"125,000～150,000円"` のような範囲表記もあり得るので、そのときは安全側として上限値を使う |
| `commonfee` | `"7,100円"` | 共益費（同上） |
| `allCount` | `"6"` | その団地の空室の総数。ページ送りの終了判定に使う |
| `roomDetailLink` | 相対パス | `https://www.ur-net.go.jp` を前に付けると部屋ページのURLになる |

### ページ送り（実測で確認済み）

八王子の「館ヶ丘」団地（空室6件）で確認した：

```
pageIndex=0 → 5件 (allCount=6, rowMax=5)
pageIndex=1 → 1件
```

取得した件数が `allCount` に達するまで `pageIndex` を増やしていく。

### 最寄り駅について

部屋のレスポンスにある `traffic` は `null`。最寄り駅と徒歩分数は、団地単位で②の `traffic` から取れる。
ただし実装では、毎時②を叩く必要がないように `config.yaml` に団地ごとに持たせる方針とする（駅情報はほぼ変わらないため）。

## ② エリア内の団地一覧 `bukken_result`

浦安市のエリアページ `https://www.ur-net.go.jp/chintai/kanto/chiba/area/227.html` は
`initSearch('kanto', '12', 'chiba', 'area', {skcs: '227'})` を呼んでいる（12=千葉県、227=浦安市）。

```
POST https://chintai.r6.ur-net.go.jp/chintai/api/bukken/result/bukken_result/

mode=area&skcs=227&block=kanto&tdfk=12&rireki_tdfk=12&orderByField=0&pageSize=50&pageIndex=0&pageIndexRoom=0&sp=
```

- **空室0件の団地も含めて**、エリア内の全団地が返る（`bukkenCount` が全団地の数）
- 各団地には `danchiNm`、`traffic`（HTMLの `<li>` 形式）、`place`、`roomCount`、`room`（先頭5件まで）が含まれる
- `room` は5件で打ち切られるため、空室監視には使わず①を使う

## 新浦安エリアの団地（2026-10-16時点）

浦安市（skcs=227）の全6団地：

| shisya | danchi | shikibetu | 団地名 | 新浦安駅からのアクセス | 当日の空室 |
|---|---|---|---|---|---|
| 30 | 312 | 0 | 浦安ニューシティ美浜西エステート | 徒歩6～12分 | 0 |
| 30 | 418 | 0 | 浦安マリナイースト２１フォーラム海風の街 | 徒歩12～14分 / バス2分 | 0 |
| 30 | 513 | 0 | 浦安マリナイースト２１潮音の街 | バス7分＋徒歩5～7分 | 0 |
| 30 | 512 | 0 | 浦安マリナイースト２１望海の街 | 徒歩22～26分 / バス7～10分 | 1 |
| 30 | 583 | 0 | 浦安マリナイースト２１海園の街 | 徒歩25～29分 / バス10分 | 1 |
| 30 | 584 | 0 | コンフォール舞浜（コンフォール浦安弁天） | 徒歩28～30分（最寄りは舞浜駅で徒歩17～20分） | 0 |

浦安市外の候補（市川市 skcs=203）：

| shisya | danchi | shikibetu | 団地名 | 新浦安駅からのアクセス |
|---|---|---|---|---|
| 30 | 336 | 0 | ハイタウン塩浜 | バス8分＋徒歩1～6分（最寄りは南行徳駅でバス5分） |

## マナー・注意点

- `www.ur-net.go.jp/robots.txt` で禁止されているのは `/chintai/*/result/?skcs=` などの検索結果HTMLだけで、APIのホスト（chintai.r6）にはrobots.txtがない（403）。
- 監視では1時間に団地数ぶん（6～7回程度、ページ送りがあれば少し増える）のPOSTを、1～2秒の間隔を空けて送るだけにする。
- User-Agentにはツール名を明示する。
- 公式に公開されたAPIではない（サイト内部用）ため、仕様は予告なく変わり得る。レスポンスが想定した形でなければ「取得失敗」として扱い、前回の状態を残す。
- 空室なしは `null` で返るので、**HTTPエラーや形の崩れたJSONと区別して**扱う（`null` は正常な「0件」）。

## 東京23区（2026-10-23 追加）

`bukken_result` を `tdfk=13`、`skcs=101`〜`123` で区ごとに呼ぶと、その区の全団地が返る（23区で計192団地、各区とも1ページ50件に収まる）。
各団地の `shisya`（東京は20）・`danchi`・`shikibetu`・`danchiNm`・`traffic`・`roomCount` を使い、
`roomCount > 0` の団地だけ ① `detail_bukken_room` で全空室を取る。団地IDを config に書かずに済み、団地の増減にも追従できる。
