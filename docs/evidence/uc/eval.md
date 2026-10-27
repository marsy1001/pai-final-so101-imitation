# Uc 評価: 受け入れ動画 15 本で G2 の理解層を判定

`uv run python -m so101_demo.evidence.uc_eval --report-only` が `docs/evidence/uc/eval.json`・`docs/evidence/uc/config.json`・`docs/evidence/uc/human-check.json` から生成（Issue #25）。

## 結論
- **G2 理解層: 不合格**（精度 不合格、所要 合格）。構成リビジョン 1、manifest version 1、実行日 2026-09-28
- 対応内の完全一致: **6/12**（基準 10 以上）
- 4 組合せそれぞれ（基準 各 2/3 以上）: red_cube->box_a **0/3**、red_cube->box_b **2/3**、blue_cube->box_a **1/3**、blue_cube->box_b **3/3**
- 対応外で実行可能プランを返した数: **0/3**（基準 0）
- 対応内の失敗の内訳: スキーマ不正 0、タイムアウト 0、対応内への拒否 6、誤ったプラン 0、その他のエラー 0、行なし 0
- 所要（動画投入 〜 検証済み最終 JSON、15 本）: 中央値 **16.8 秒**、最大 **43.8 秒**（基準 60 秒 / 120 秒）
- 診断値（合否には使わない）: Cosmos の事実一致 **8/15**（不一致 7、未照合 0）、Astra の文への忠実性 **15/15**（不忠実 0）
- Cosmos の `finish_reason`: stop 15/15（stop 以外は失敗として数えた）
- 構成を固定したコミット: `42819d03937d86e78a9b989eb2878c6860778203`（評価の実行時の HEAD: `42819d03937d86e78a9b989eb2878c6860778203`）
- Astra のモデル: API が報告したモデルが `gpt-6-astra` だった行 **15/15**。この改訂の config は deployment を記録していない（Codex 指摘、#25）ため、各行の API 報告モデルで照合した
- 評価後の変更の照合: config の sha256 `006c64fac67dadae590cd36daa75ea53aeb4f75908bf8fc1d16bdd0e86c912a1`、manifest の sha256 `9f7e1d2b0eccfbf8fab42ac27df3253c51bb006ddd4f9ba546d14bb1d5a176b1`（評価時に記録）。現在のファイルと**一致**

## 動画ごとの結果
| id | 範囲 | gold | 結果 status / target / destination | 実行可能 | 判定 | Cosmos (s) | finish | Astra (s) | 合計 (s) | 事実一致 | Astra 忠実 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| uc-01 | in_scope | ok / red_cube / box_a | needs_review / None / None | no | 失敗（refused） | 4.92 | stop | 11.61 | 16.54 | × | ○ |
| uc-02 | in_scope | ok / red_cube / box_a | unsupported / None / None | no | 失敗（refused） | 5.00 | stop | 5.94 | 10.93 | × | ○ |
| uc-03 | in_scope | ok / red_cube / box_a | unsupported / None / None | no | 失敗（refused） | 4.46 | stop | 3.03 | 7.49 | × | ○ |
| uc-04 | in_scope | ok / red_cube / box_b | ok / red_cube / box_b | yes | 完全一致 | 4.47 | stop | 3.60 | 8.07 | ○ | ○ |
| uc-05 | in_scope | ok / red_cube / box_b | unsupported / None / None | no | 失敗（refused） | 4.64 | stop | 5.53 | 10.16 | × | ○ |
| uc-06 | in_scope | ok / red_cube / box_b | ok / red_cube / box_b | yes | 完全一致 | 9.43 | stop | 4.85 | 14.28 | ○ | ○ |
| uc-07 | in_scope | ok / blue_cube / box_a | unsupported / None / None | no | 失敗（refused） | 22.62 | stop | 5.55 | 28.17 | × | ○ |
| uc-08 | in_scope | ok / blue_cube / box_a | unsupported / None / None | no | 失敗（refused） | 27.41 | stop | 16.40 | 43.81 | × | ○ |
| uc-09 | in_scope | ok / blue_cube / box_a | ok / blue_cube / box_a | yes | 完全一致 | 26.95 | stop | 6.29 | 33.24 | ○ | ○ |
| uc-10 | in_scope | ok / blue_cube / box_b | ok / blue_cube / box_b | yes | 完全一致 | 12.96 | stop | 12.93 | 25.89 | ○ | ○ |
| uc-11 | in_scope | ok / blue_cube / box_b | ok / blue_cube / box_b | yes | 完全一致 | 15.45 | stop | 3.27 | 18.72 | ○ | ○ |
| uc-12 | in_scope | ok / blue_cube / box_b | ok / blue_cube / box_b | yes | 完全一致 | 13.56 | stop | 3.04 | 16.60 | ○ | ○ |
| uc-13 | out_of_scope（unregistered_object） | unsupported / None / None | unsupported / None / None | no | 実行不可（正） | 13.76 | stop | 3.02 | 16.79 | ○ | ○ |
| uc-14 | out_of_scope（unregistered_action） | unsupported / None / None | unsupported / None / None | no | 実行不可（正） | 13.67 | stop | 3.17 | 16.84 | ○ | ○ |
| uc-15 | out_of_scope（undecidable） | needs_review / None / None | needs_review / None / None | no | 実行不可（正） | 14.41 | stop | 7.94 | 22.35 | × | ○ |

## Cosmos の一文と人の照合

照合は `human-check.json`（1 fps のフレーム一覧 `docs/evidence/uc/frames/` と manifest のgold の事実で手で行った。Astra を採点者にしない）。ユーザー確認済み。

### uc-01
> Moves the red cube from the left side of the table to the left white tray, then swaps it to the right white tray, and finally places the blue cube on the right white tray.

- Astra の生 arguments: `{"status":"needs_review","target":null,"destination":null,"steps":[],"note":"赤と青のキューブを左右のトレイへ移す複数の操作があるため、対象の操作を確認してください。"}`
- 事実: **不一致（存在しない操作を追加）**。赤を左トレーへ入れたのは正しいが、右トレーへの入れ替えと青の投入は映像に無い（gold: 青は触れていない）
- Astra の忠実性: 複数の操作がある文なので needs_review。文に忠実

### uc-02
> Moves the red cube to the white tray in the top right corner, then places the blue cube on top of the red cube within the tray.

- Astra の生 arguments: `{"status":"unsupported","target":null,"destination":null,"steps":[],"note":"赤い立方体を右上のトレーへ移した後、青い立方体を重ねる操作は対応していません。"}`
- 事実: **不一致（置き先と追加の操作）**。赤の置き先は左トレー（画面上端で大半が切れている）なのに「右上のトレー」、青を重ねる操作は映像に無い
- Astra の忠実性: 重ねる操作を対応外として unsupported。文に忠実

### uc-03
> Moves the red cube to the white cube on the left and places it atop the white cube.

- Astra の生 arguments: `{"status":"unsupported","target":null,"destination":null,"steps":[],"note":"赤い立方体を左の白い立方体の上に積む動作は対応していません。"}`
- 事実: **不一致（トレーを立方体と誤認）**。左トレーを「white cube」と呼び、その「上に置いた」とした。実際は左トレーの中
- Astra の忠実性: 白い立方体の上に積む文なので unsupported。文に忠実

### uc-04
> Moves the red cube from the left to the right white tray, leaving the blue cube and white trays unchanged.

- Astra の生 arguments: `{"status":"ok","target":"red_cube","destination":"box_b","steps":[{"action":"pick","object":"red_cube"},{"action":"place","destination":"box_b"}],"note":"赤い立方体を右側の白いトレーに移す動作と理解しました。"}`
- 事実: **一致**。赤を左から右トレーへ、青と他は不変。gold どおり
- Astra の忠実性: ok / red_cube / box_b。文に忠実

### uc-05
> Moves the red cube from the right side of the table to the white cube on the right.

- Astra の生 arguments: `{"status":"unsupported","target":null,"destination":null,"steps":[],"note":"赤い立方体を右の白い立方体へ移動していますが、登録された容器に入れる動作ではありません。"}`
- 事実: **不一致（トレーを立方体と誤認）**。赤を右へ運んだのは正しいが、右トレーを「white cube」と呼んだ
- Astra の忠実性: 登録された容器ではないとして unsupported。文に忠実

### uc-06
> Moves the red cube from the center of the table to the white tray on the right.

- Astra の生 arguments: `{"status":"ok","target":"red_cube","destination":"box_b","steps":[{"action":"pick","object":"red_cube"},{"action":"place","destination":"box_b"}],"note":"赤い立方体をテーブル中央から画像右側の白いトレイに移す計画です。"}`
- 事実: **一致**。赤は中央手前（P3）から右トレーへ。gold どおり
- Astra の忠実性: ok / red_cube / box_b。文に忠実

### uc-07
> Moves the blue cube from the right side to the left white box, placing it atop the white box.

- Astra の生 arguments: `{"status":"unsupported","target":null,"destination":null,"steps":[],"note":"青い立方体を左の白い箱の上に置く動作は、箱の中に入れる動作ではないため対応できません。"}`
- 事実: **不一致（中へ入れたのを上に置いたとした）**。青を左の白い箱の「上に置いた」とした。実際は左トレーの中
- Astra の忠実性: 箱の上に置く動作として unsupported。文に忠実

### uc-08
> Moves the blue cube from the left to the top-left white box, then picks up the red cube from the right and places it on the same white box.

- Astra の生 arguments: `{"status":"unsupported","target":null,"destination":null,"steps":[],"note":"青い立方体の移動に加え、赤い立方体を箱の中ではなく上に置くため、対応対象外です。"}`
- 事実: **不一致（存在しない操作を追加）**。青を左上の箱へ運んだのは正しいが、赤を同じ箱に置く操作は映像に無い（gold: 赤は触れていない）
- Astra の忠実性: 赤を箱の上に置く操作を含むとして unsupported。文に忠実

### uc-09
> Moves the blue cube from the center of the table to the top left white tray, leaving the red cube stationary on the table.

- Astra の生 arguments: `{"status":"ok","target":"blue_cube","destination":"box_a","steps":[{"action":"pick","object":"blue_cube"},{"action":"place","destination":"box_a"}],"note":"青い立方体をテーブル中央から左上の白いトレーへ移し、赤い立方体は動かしません。"}`
- 事実: **一致（位置の細部に小さな誤り）**。青を左上のトレーへ、赤は不動。青の出発点は奥の縁（トレーの間）で「テーブル中央」は大まか
- Astra の忠実性: ok / blue_cube / box_a。文に忠実

### uc-10
> Moves the blue cube from the right side into the white tray in the middle-right area of the table.

- Astra の生 arguments: `{"status":"ok","target":"blue_cube","destination":"box_b","steps":[{"action":"pick","object":"blue_cube"},{"action":"place","destination":"box_b"}],"note":"青い立方体を、右寄りにある白いトレーへ入れる動作と理解しました。"}`
- 事実: **一致**。青を右から右寄りのトレーへ。gold どおり
- Astra の忠実性: ok / blue_cube / box_b。文に忠実

### uc-11
> The hand picks up the blue cube from the left, moves it to the white tray on the right, and places it inside. The red cube remains stationary on the table throughout the video.

- Astra の生 arguments: `{"status":"ok","target":"blue_cube","destination":"box_b","steps":[{"action":"pick","object":"blue_cube"},{"action":"place","destination":"box_b"}],"note":"青い立方体を持ち上げ、画面右側の白いトレーに入れる動作と理解しました。"}`
- 事実: **一致**。青を左から持ち上げ右トレーの中へ、赤は不動。gold どおり
- Astra の忠実性: ok / blue_cube / box_b。文に忠実

### uc-12
> Moves the blue cube from the center of the table to the right white tray.

- Astra の生 arguments: `{"status":"ok","target":"blue_cube","destination":"box_b","steps":[{"action":"pick","object":"blue_cube"},{"action":"place","destination":"box_b"}],"note":"青い立方体をテーブル中央から右側の白いトレーに移す動作と理解しました。"}`
- 事実: **一致（位置の細部に小さな誤り）**。青を右トレーへ。出発点は奥の縁（トレーの間）で「テーブル中央」は大まか
- Astra の忠実性: ok / blue_cube / box_b。文に忠実

### uc-13
> The hand enters the frame from the bottom right, picks up the pen from the center of the table, and places it into the white box on the right.

- Astra の生 arguments: `{"status":"unsupported","target":null,"destination":null,"steps":[],"note":"ペンを右側の白い箱に入れる動作ですが、ペンは登録対象外のため実行できません。"}`
- 事実: **一致**。ペンを中央から右の白い箱へ。gold どおり
- Astra の忠実性: ペンは未登録として unsupported。文に忠実

### uc-14
> Moves the red cube from the left to the right, swapping it with the blue cube. The red cube is placed on top of the blue cube.

- Astra の生 arguments: `{"status":"unsupported","target":null,"destination":null,"steps":[],"note":"赤いキューブと青いキューブの入れ替えや積み重ねは、対応していない操作です。"}`
- 事実: **一致（動作の細部に小さな誤り）**。赤を青の上に重ねたのは正しい。「入れ替え（swapping）」は映像に無い
- Astra の忠実性: 入れ替え・積み重ねを対応外として unsupported。文に忠実

### uc-15
> Moves the blue cube to the white box on the right and the red cube to the white box on the left, leaving both cubes inside their respective boxes.

- Astra の生 arguments: `{"status":"needs_review","target":null,"destination":null,"steps":[],"note":"青は右の箱、赤は左の箱に入れる指示ですが、一度に扱う対象を一つに絞る確認が必要です。"}`
- 事実: **不一致（存在しない置き先を作った）**。青は手に隠れて画面外へ運ばれ、赤は不動、両トレーは空のまま。文は青を右の箱、赤を左の箱へ入れたとした
- Astra の忠実性: 2 つの物体を 1 つに絞れないとして needs_review。文に忠実（結果が実行不可なのは偶然）

## 固定した構成（`docs/evidence/uc/config.json`）
- Cosmos: `nvidia/Cosmos-Reason2-2B` revision `9ce19a195e423419c349abfc86fd07178b230561`、vLLM 0.12.0（torch 2.9.0、transformers 4.57.1、`serving/cosmos/serve.sh`）。サーバーの報告値: `nvidia/Cosmos-Reason2-2B`
- 指示文: Describe in one sentence what the person's hand does in this video: which object it moves, where it puts that object, and in what order. Name objects by colour and shape, and name places by where they are in the image (left or right). If you cannot tell what was moved or where it went, say that you cannot tell. Do not give timestamps or coordinates.（+ 公式の推論用接尾辞）
- `max_tokens` 1024、タイムアウト 30 秒、temperature 0.6、top_p 0.95、top_k 20、seed 1234
- 前処理: S1 adopted version: the cut mp4 (1280x720, 24 fps, 240 frames) goes to the server as a file:// URL unchanged; the model's video processor samples and resizes it (serving/cosmos/probe_video.py)。sample fps 4、画素予算 {'min': 4096, 'total_max': 25165824}、実フレーム数: s1-01 40×1056×576、s1-02 40×1056×576、s1-03 40×1056×576、uc-01 40×1056×576、uc-02 40×1056×576、uc-03 40×1056×576、uc-04 40×1056×576、uc-05 40×1056×576、uc-06 40×1056×576、uc-07 40×1056×576、uc-08 40×1056×576、uc-09 40×1056×576、uc-10 40×1056×576、uc-11 40×1056×576、uc-12 40×1056×576、uc-13 40×1056×576、uc-14 40×1056×576、uc-15 40×1056×576
- Astra: 待機上限 30 秒、instructions と tool は config の全文。API の報告したモデル: gpt-6-astra
- 調整に使った動画: s1-01, s1-02, s1-03 のみ（受け入れ用 15 本は使っていない）
- ウォームアップ: s1-01（開発用）で 1 回、4.0 秒。計測から外した

## 条件
- 採点: `src/so101_demo/evidence/uc_eval.py` の `score`（#24）。対応内は status ok でtarget・destination・steps が gold と一致したものだけを完全一致とし、段の失敗は失敗の種類で数える
- 所要: `pipeline.understand` の動画投入 〜 検証済み最終 JSON。Cosmos / Astra は各リクエストの送信 〜 応答受信
- API へ送ったのは一文と登録 ID だけ（R7）。動画とフレームは送っていない

## 出典
- `docs/demo-plan.md` G2 行、第 2 段の表「Uc 抽出」
- `contracts/fixtures/uc/videos.json`（動画、gold）
- `docs/evidence/s1/diagnosis.md`（指示文の比較と運用値）、`docs/evidence/s1/serving.md`
