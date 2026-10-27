# Uc 評価（改訂 2）: 受け入れ動画 15 本で G2 の理解層を判定

`uv run python -m so101_demo.evidence.uc_eval --report-only` が `docs/evidence/uc/eval-r2.json`・`docs/evidence/uc/config-r2.json`・`docs/evidence/uc/human-check-r2.json` から生成。

## 結論
- **G2 理解層: 不合格**（精度 不合格、所要 合格）。構成リビジョン 2、manifest version 1、実行日 2026-09-29
- 対応内の完全一致: **8/12**（基準 10 以上）
- 4 組合せそれぞれ（基準 各 2/3 以上）: red_cube->box_a **1/3**、red_cube->box_b **2/3**、blue_cube->box_a **2/3**、blue_cube->box_b **3/3**
- 対応外で実行可能プランを返した数: **1/3**（基準 0）（uc-15）
- 対応内の失敗の内訳: スキーマ不正 0、タイムアウト 0、対応内への拒否 2、誤ったプラン 1、その他のエラー 1、行なし 0
- 所要（動画投入 〜 検証済み最終 JSON、15 本）: 中央値 **19.7 秒**、最大 **33.2 秒**（基準 60 秒 / 120 秒）
- 診断値（合否には使わない）: Cosmos の事実一致 **10/14**（不一致 4、未照合 0）、Astra の文への忠実性 **14/14**（不忠実 0）
- Cosmos の `finish_reason`: stop 14/15（stop 以外は失敗として数えた）
- 構成を固定したコミット: `72fe3366eaacb57d4851bb594ac6376273f642a4`（評価の実行時の HEAD: `72fe3366eaacb57d4851bb594ac6376273f642a4`）
- Astra のモデル: API が報告したモデルが `gpt-6-astra` だった行 **14/14**。この改訂の config は deployment を記録していない（レビュー指摘）ため、各行の API 報告モデルで照合した
- 評価後の変更の照合: config の sha256 `2bbbe753c3c1e45b0d04a226535b57cbab772aa42adc6312d1a5895c3be2316d`、manifest の sha256 `9f7e1d2b0eccfbf8fab42ac27df3253c51bb006ddd4f9ba546d14bb1d5a176b1`（評価時に記録）。現在のファイルと**一致**

## 動画ごとの結果
| id | 範囲 | gold | 結果 status / target / destination | 実行可能 | 判定 | Cosmos (s) | finish | Astra (s) | 合計 (s) | 事実一致 | Astra 忠実 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| uc-01 | in_scope | ok / red_cube / box_a | needs_review / None / None | no | 失敗（refused） | 14.56 | stop | 5.29 | 19.86 | × | ○ |
| uc-02 | in_scope | ok / red_cube / box_a | ok / red_cube / box_a | yes | 完全一致 | 14.97 | stop | 4.41 | 19.38 | ○ | ○ |
| uc-03 | in_scope | ok / red_cube / box_a | needs_review / None / None | no | 失敗（refused） | 16.12 | stop | 4.85 | 20.97 | × | ○ |
| uc-04 | in_scope | ok / red_cube / box_b | ok / red_cube / box_a | yes | 失敗（wrong_plan） | 15.22 | stop | 3.39 | 18.61 | × | ○ |
| uc-05 | in_scope | ok / red_cube / box_b | ok / red_cube / box_b | yes | 完全一致 | 14.74 | stop | 3.86 | 18.60 | ○ | ○ |
| uc-06 | in_scope | ok / red_cube / box_b | ok / red_cube / box_b | yes | 完全一致 | 15.51 | stop | 4.08 | 19.59 | ○ | ○ |
| uc-07 | in_scope | ok / blue_cube / box_a | error / None / None | no | 失敗（other_error） | 24.80 | length | — | 24.80 | 未照合 | — |
| uc-08 | in_scope | ok / blue_cube / box_a | ok / blue_cube / box_a | yes | 完全一致 | 15.18 | stop | 3.21 | 18.39 | ○ | ○ |
| uc-09 | in_scope | ok / blue_cube / box_a | ok / blue_cube / box_a | yes | 完全一致 | 26.62 | stop | 3.87 | 30.48 | ○ | ○ |
| uc-10 | in_scope | ok / blue_cube / box_b | ok / blue_cube / box_b | yes | 完全一致 | 28.74 | stop | 2.86 | 31.61 | ○ | ○ |
| uc-11 | in_scope | ok / blue_cube / box_b | ok / blue_cube / box_b | yes | 完全一致 | 29.00 | stop | 4.19 | 33.20 | ○ | ○ |
| uc-12 | in_scope | ok / blue_cube / box_b | ok / blue_cube / box_b | yes | 完全一致 | 15.39 | stop | 2.89 | 18.28 | ○ | ○ |
| uc-13 | out_of_scope（unregistered_object） | unsupported / None / None | unsupported / None / None | no | 実行不可（正） | 16.16 | stop | 3.53 | 19.69 | ○ | ○ |
| uc-14 | out_of_scope（unregistered_action） | unsupported / None / None | unsupported / None / None | no | 実行不可（正） | 15.72 | stop | 3.71 | 19.43 | ○ | ○ |
| uc-15 | out_of_scope（undecidable） | needs_review / None / None | ok / blue_cube / box_a | yes | 実行可能を返した（誤受理） | 14.45 | stop | 6.25 | 20.70 | × | ○ |

## Cosmos の一文と人の照合

照合は `human-check-r2.json`（1 fps のフレーム一覧 `docs/evidence/uc/frames/` と manifest のgold の事実で手で行った。Astra を採点者にしない）。ユーザー確認済み。

### uc-01
> Moves the red cube to the white tray on the left and the blue cube to the white tray on the right.

- Astra の生 arguments: `{"status":"needs_review","target":null,"destination":null,"steps":[],"note":"赤い立方体を左のトレーへ、青い立方体を右のトレーへ移す記述のため、どちらを行うか確認が必要です。"}`
- 事実: **不一致（存在しない操作を追加）**。赤を左トレーへは正しい。青を右トレーへは映像に無い（gold: 青は触れていない）
- Astra の忠実性: 2 つの操作を 1 つに絞れないとして needs_review。文に忠実

### uc-02
> Moves the red cube from the right side of the table to the left white tray.

- Astra の生 arguments: `{"status":"ok","target":"red_cube","destination":"box_a","steps":[{"action":"pick","object":"red_cube"},{"action":"place","destination":"box_a"}],"note":"赤い立方体をテーブルの右側から左の白いトレーに移すと理解しました。"}`
- 事実: **一致**。赤を右側から左トレーへ。gold どおり
- Astra の忠実性: ok / red_cube / box_a。文に忠実

### uc-03
> Moves the red cube to the white tray on the left and then the blue cube to the white tray on the right.

- Astra の生 arguments: `{"status":"needs_review","target":null,"destination":null,"steps":[],"note":"赤い立方体を左のトレーへ、青い立方体を右へ移す指定ですが、1件ずつの確認が必要です。"}`
- 事実: **不一致（存在しない操作を追加）**。赤を左トレーへは正しい。青を右トレーへは映像に無い（gold: 青は奥の縁で触れていない）
- Astra の忠実性: needs_review。文に忠実

### uc-04
> The person's hand picks up the red cube, moves it to the white tray on the left, and places it inside.

- Astra の生 arguments: `{"status":"ok","target":"red_cube","destination":"box_a","steps":[{"action":"pick","object":"red_cube"},{"action":"place","destination":"box_a"}],"note":"赤い立方体を持ち上げ、画像左側の白いトレイに入れる動作です。"}`
- 事実: **不一致（左右の取り違え）**。赤の置き先は右トレー（gold）なのに「左のトレー」とした
- Astra の忠実性: ok / red_cube / box_a。文に忠実（文の誤りがそのままプランに入った）

### uc-05
> Moves the red cube from the right side of the table to the white tray on the right edge.

- Astra の生 arguments: `{"status":"ok","target":"red_cube","destination":"box_b","steps":[{"action":"pick","object":"red_cube"},{"action":"place","destination":"box_b"}],"note":"赤い立方体をテーブルの右側から右端の白いトレーへ移す動作と理解しました。"}`
- 事実: **一致**。赤を右側から右トレーへ。gold どおり
- Astra の忠実性: ok / red_cube / box_b。文に忠実

### uc-06
> The person's hand picks up the red cube from the foreground, moves it to the right white tray, and places it inside.

- Astra の生 arguments: `{"status":"ok","target":"red_cube","destination":"box_b","steps":[{"action":"pick","object":"red_cube"},{"action":"place","destination":"box_b"}],"note":"赤い立方体を持ち上げ、画面右側の白いトレーに入れたと理解しました。"}`
- 事実: **一致**。赤を手前中央から右トレーへ。gold どおり
- Astra の忠実性: ok / red_cube / box_b。文に忠実

### uc-07
> (なし: finish_reason='length' (not 'stop'))

- Astra の生 arguments: `(呼んでいない)`
- 未照合

### uc-08
> Moves the blue cube from the left side to the white tray on the left, then exits the frame while leaving the red cube untouched on the right.

- Astra の生 arguments: `{"status":"ok","target":"blue_cube","destination":"box_a","steps":[{"action":"pick","object":"blue_cube"},{"action":"place","destination":"box_a"}],"note":"青い立方体を画面左側の白いトレーに入れる動作と理解しました。"}`
- 事実: **一致**。青を左側から左トレーへ、赤は右で不動。「exits the frame」は手が出ていくことで正しい
- Astra の忠実性: ok / blue_cube / box_a。文に忠実

### uc-09
> The person's hand picks up the blue cube from the center of the table and places it onto the white tray on the left edge of the table.

- Astra の生 arguments: `{"status":"ok","target":"blue_cube","destination":"box_a","steps":[{"action":"pick","object":"blue_cube"},{"action":"place","destination":"box_a"}],"note":"青い立方体を持ち上げ、左側の白いトレーに置く動作と理解しました。"}`
- 事実: **一致（位置の細部に小さな誤り）**。青を左トレーへ。出発点は奥の縁（トレーの間）で「テーブル中央」は大まか
- Astra の忠実性: ok / blue_cube / box_a。文に忠実

### uc-10
> Moves the blue cube from the right side of the table to the right white tray

- Astra の生 arguments: `{"status":"ok","target":"blue_cube","destination":"box_b","steps":[{"action":"pick","object":"blue_cube"},{"action":"place","destination":"box_b"}],"note":"青い立方体をテーブルの右側から右の白いトレーに移す動作と理解しました。"}`
- 事実: **一致**。青を右側から右トレーへ。gold どおり
- Astra の忠実性: ok / blue_cube / box_b。文に忠実

### uc-11
> Moves the blue cube from the left side of the table to the white tray on the right.

- Astra の生 arguments: `{"status":"ok","target":"blue_cube","destination":"box_b","steps":[{"action":"pick","object":"blue_cube"},{"action":"place","destination":"box_b"}],"note":"青い立方体をテーブルの左側から右側の白いトレーに移す動作と理解しました。"}`
- 事実: **一致**。青を左側から右トレーへ。gold どおり
- Astra の忠実性: ok / blue_cube / box_b。文に忠実

### uc-12
> The person's hand enters from the right, grasps the blue cube, lifts it, and places it into the white tray on the right side of the table.

- Astra の生 arguments: `{"status":"ok","target":"blue_cube","destination":"box_b","steps":[{"action":"pick","object":"blue_cube"},{"action":"place","destination":"box_b"}],"note":"青い立方体を持ち上げ、右側の白いトレーに入れる動作と理解しました。"}`
- 事実: **一致**。手が右から入り、青を持ち上げて右トレーへ。gold どおり
- Astra の忠実性: ok / blue_cube / box_b。文に忠実

### uc-13
> The person's hand enters from the bottom right, picks up the pen, moves it to the white tray on the right, and places it there.

- Astra の生 arguments: `{"status":"unsupported","target":null,"destination":null,"steps":[],"note":"ペンを右側の白いトレーに置いていますが、ペンは登録対象外のため実行できません。"}`
- 事実: **一致**。ペンを右トレーへ。gold どおり
- Astra の忠実性: ペンは未登録として unsupported。文に忠実

### uc-14
> The person's hand moves the red cube from the left side of the table to the blue cube on the right, stacking it on top of the blue cube.

- Astra の生 arguments: `{"status":"unsupported","target":null,"destination":null,"steps":[],"note":"赤い立方体を青い立方体の上に積む動作は、対応していません。"}`
- 事実: **一致**。赤を青の上に積んだ。gold どおり
- Astra の忠実性: 積み重ねを対応外として unsupported。文に忠実

### uc-15
> Moves the blue cube to the white tray on the left side of the table

- Astra の生 arguments: `{"status":"ok","target":"blue_cube","destination":"box_a","steps":[{"action":"pick","object":"blue_cube"},{"action":"place","destination":"box_a"}],"note":"青い立方体を左側の白いトレーに入れる動作と理解しました。"}`
- 事実: **不一致（見えない置き先をトレーで埋めた）**。青は手に隠れて右端から画面外へ運ばれ、両トレーは空のまま（gold）。文は「左のトレーへ」とした
- Astra の忠実性: ok / blue_cube / box_a。文に忠実（文の誤りで誤受理になった）

## 固定した構成（`docs/evidence/uc/config-r2.json`）
- Cosmos: `nvidia/Cosmos-Reason2-2B` revision `9ce19a195e423419c349abfc86fd07178b230561`、vLLM 0.12.0（torch 2.9.0、transformers 4.57.1、`serving/cosmos/serve.sh`）。サーバーの報告値: `nvidia/Cosmos-Reason2-2B`
- 指示文: The table shows a red cube, a blue cube, and two white trays at the far edge of the table, one on the left and one on the right of the image. Describe in one sentence what the person's hand does in this video: which object it moves, where it puts that object, and in what order. Name objects by colour and shape, and name places by where they are in the image (left or right). If you cannot tell what was moved or where it went, say that you cannot tell. Do not give timestamps or coordinates.（+ 公式の推論用接尾辞）
- `max_tokens` 1024、タイムアウト 30 秒、temperature 0.6、top_p 0.95、top_k 20、seed 1234
- 前処理: S1 adopted version: the cut mp4 (1280x720, 24 fps, 240 frames) goes to the server as a file:// URL unchanged; the model's video processor samples and resizes it (serving/cosmos/probe_video.py)。sample fps 4、画素予算 {'min': 4096, 'total_max': 25165824}、実フレーム数: s1-01 40×1056×576、s1-02 40×1056×576、s1-03 40×1056×576、uc-01 40×1056×576、uc-02 40×1056×576、uc-03 40×1056×576、uc-04 40×1056×576、uc-05 40×1056×576、uc-06 40×1056×576、uc-07 40×1056×576、uc-08 40×1056×576、uc-09 40×1056×576、uc-10 40×1056×576、uc-11 40×1056×576、uc-12 40×1056×576、uc-13 40×1056×576、uc-14 40×1056×576、uc-15 40×1056×576
- Astra: 待機上限 30 秒、instructions と tool は config の全文。API の報告したモデル: gpt-6-astra
- 調整: 受け入れ用 15 本の前の版の結果を見て指示文を直した（2026-09-29）。この版の点数は受け入れ用に合わせ込んだ後の値で、初見の動画では下がりうる。合わせ込む前の点数は改訂 1（`eval.md`）
- ウォームアップ: s1-01（開発用）で 1 回、15.6 秒。計測から外した

## 条件
- 採点: `src/so101_demo/evidence/uc_eval.py` の `score`。対応内は status ok でtarget・destination・steps が gold と一致したものだけを完全一致とし、段の失敗は失敗の種類で数える
- 所要: `pipeline.understand` の動画投入 〜 検証済み最終 JSON。Cosmos / Astra は各リクエストの送信 〜 応答受信
- API へ送ったのは一文と登録 ID だけ（R7）。動画とフレームは送っていない

## 出典
- `docs/brief.md`「問い・スコープ」問い 2（理解層の精度と失敗の型）
- `contracts/fixtures/uc/videos.json`（動画、gold）
- `docs/evidence/s1/diagnosis.md`（指示文の比較と運用値）、`docs/evidence/s1/serving.md`
