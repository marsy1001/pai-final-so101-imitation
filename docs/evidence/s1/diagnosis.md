# S1 診断: 10 秒動画 3 本で Cosmos 一文 → Astra 契約 JSON が通るか

`uv run python -m so101_demo.evidence.s1_diagnosis` が生成（GPU 機）。生データは `diagnosis.json`、照合は `human-check.json`（手書き）。

## 結論
- 実行日 2026-09-28、commit `8150f07`。動画 3 本 × 3 回 = 9 実行（ウォームアップ 1 回は別）
- Cosmos: 9/9 実行で一文を返した（finish_reason stop 9/9、失敗 0）。同じ動画の 3 回で文が一字一句同じだった動画: 3/3（seed 固定でも再現は保証されない。「プロンプトの比較」）
- Astra: 呼んだのは 9/9 実行、契約 schema の検証を **9/9** 通った
- 対応外の動画から実行可能プランが返った件数: **0**
- 段ごとの成否（Cosmos は人の照合、Astra は契約検証、最終は gold との比較）:
  - s1-01（in_scope）: Cosmos 成立 / Astra 成立（契約検証 3/3） / 最終 gold と一致
  - s1-02（in_scope）: Cosmos 成立 / Astra 成立（契約検証 3/3） / 最終 gold と一致
  - s1-03（out_of_scope）: Cosmos 成立 / Astra 成立（契約検証 3/3） / 最終 gold と一致
- **未成立の段: なし**。詳細は各動画の「照合」
- 所要（ウォームアップ後）: Cosmos 中央値 3.2 秒、最大 3.6 秒、Astra 中央値 3.5 秒、最大 6.5 秒、動画投入 〜 検証済み最終 JSON 中央値 6.7 秒、最大 9.5 秒。ウォームアップ 1 回は 3.6 秒（起動直後の初回なら約 19 秒。この実行ではサーバーが既に温まっていた場合がある。`serving.md`）
- 運用値は暫定値のまま固定: Cosmos `max_tokens` 1024、Cosmos タイムアウト 30 秒、Astra 待機上限 30 秒（根拠は「運用値」）

## 運用値（demo-plan「未決の値」4）
| 値 | 固定値 | 実測 | 根拠 |
|---|---|---|---|
| Cosmos `max_tokens` | 1024 | 完了トークン最大 246（推論トレース込み） | 最大の 4.2 倍の余裕。生成は約 115 tokens/s（`serving.md`）なので、暴走しても約 9 秒で `length` で止まり、`finish_reason` 検査で失敗になる |
| Cosmos タイムアウト | 30 秒 | 最大 3.6 秒（ウォームアップ除く） | 暴走の上限（プリフィル約 1 秒 + 1024 トークン 約 9 秒）の約 3 倍。ウォームアップ（約 19 秒）もこの中に収まる |
| Astra 待機上限 | 30 秒 | 最大 6.5 秒（`astra-roundtrip.md` の人手の文では最大 5.7 秒） | 最大の 3 倍以上。Cosmos の上限と足しても 60 秒で、動画投入 〜 最終 JSON の実測最大の 5 倍以上 |

この 3 本・9 実行では Cosmos の暴走（`length`）は 1 度も出ていない。上限が実際に効くかは、毎回違う動画を使う Uc で見る。

## s1-01（in_scope）
- 動画: `docs/evidence/s1/videos/s1-01-red-to-box_a.mp4`（sha256 `edaee784b5ef…`）
- gold: ok / red_cube / box_a。事実: Red cube front left, blue cube front centre, both trays empty. A right hand grasps the red cube, lifts it and puts it into the left tray; the hand leaves. The blue cube is not touched. Completed: the red cube rests in the left tray at the end.
- 前処理（モデルの video processor の実測）: 元 240 フレーム 1280×720 @ 24 fps → sample fps 4 で **40 フレーム**、1 フレーム **1056×576**。画素予算は全フレーム合計 25,165,824 画素（下限 4,096）で、それに合わせて縮小される。動画トークン 11,880（サーバーの prompt_tokens は 12,151）

| run | Cosmos (s) | finish_reason | 完了トークン | Astra (s) | API status | 契約検証 | 結果 status / target / destination | 実行可能 | gold 一致 | 合計 (s) |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 3.17 | stop | 246 | 3.15 | completed | pass | ok / red_cube / box_a | yes | ○ | 6.32 |
| 2 | 3.54 | stop | 246 | 3.35 | completed | pass | ok / red_cube / box_a | yes | ○ | 6.90 |
| 3 | 3.48 | stop | 246 | 3.24 | completed | pass | ok / red_cube / box_a | yes | ○ | 6.72 |

Cosmos の一文（全回同じ）:

> The hand picks up the red cube from the center of the table, lifts it, and places it into the left white tray, leaving the blue cube stationary on the right.

Astra の生 tool call arguments:

- run 1: `{"status":"ok","target":"red_cube","destination":"box_a","steps":[{"action":"pick","object":"red_cube"},{"action":"place","destination":"box_a"}],"note":"赤い立方体を持ち上げて、画像左側の白いトレーに入れる動作です。"}`
- run 2: `{"status":"ok","target":"red_cube","destination":"box_a","steps":[{"action":"pick","object":"red_cube"},{"action":"place","destination":"box_a"}],"note":"赤い立方体を持ち上げて、画面左側の白いトレーに入れる動作と理解しました。"}`
- run 3: `{"status":"ok","target":"red_cube","destination":"box_a","steps":[{"action":"pick","object":"red_cube"},{"action":"place","destination":"box_a"}],"note":"赤い立方体を持ち上げ、画面左側の白いトレーに入れる動作と理解しました。"}`

照合（gold と映像、ユーザー確認済み）:

- 判定: **一致（位置の細部に小さな誤り）**
- 対象: ○ red cube
- 置き先: ○ left white tray (box_a)
- 順序: ○ pick → lift → put in
- 完了 / 未完了: ○ ends inside the left tray
- 落とした曖昧さ: none (the video is unambiguous)
- 映像に無い事実: minor: 'from the center of the table'. The red cube starts front left (gold). 'blue cube stationary on the right' is true relative to the red cube (gold: front centre). The target and destination do not depend on it

## s1-02（in_scope）
- 動画: `docs/evidence/s1/videos/s1-02-blue-to-box_b.mp4`（sha256 `65807d10a981…`）
- gold: ok / blue_cube / box_b。事実: Separate take from s1-01 with other start positions: blue cube front left, red cube at the right edge of the frame (partly cut off), both trays empty. A right hand grasps the blue cube, carries it across and puts it into the right tray; the hand leaves. The red cube is not touched. Completed: the blue cube rests in the right tray at the end.
- 前処理（モデルの video processor の実測）: 元 240 フレーム 1280×720 @ 24 fps → sample fps 4 で **40 フレーム**、1 フレーム **1056×576**。画素予算は全フレーム合計 25,165,824 画素（下限 4,096）で、それに合わせて縮小される。動画トークン 11,880（サーバーの prompt_tokens は 12,151）

| run | Cosmos (s) | finish_reason | 完了トークン | Astra (s) | API status | 契約検証 | 結果 status / target / destination | 実行可能 | gold 一致 | 合計 (s) |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 2.43 | stop | 160 | 3.45 | completed | pass | ok / blue_cube / box_b | yes | ○ | 5.88 |
| 2 | 2.70 | stop | 160 | 3.58 | completed | pass | ok / blue_cube / box_b | yes | ○ | 6.28 |
| 3 | 2.60 | stop | 160 | 6.53 | completed | pass | ok / blue_cube / box_b | yes | ○ | 9.13 |

Cosmos の一文（全回同じ）:

> The person's hand touches the blue cube, lifts it, and moves it to the right white tray. The red cube remains stationary on the table.

Astra の生 tool call arguments:

- run 1: `{"status":"ok","target":"blue_cube","destination":"box_b","steps":[{"action":"pick","object":"blue_cube"},{"action":"place","destination":"box_b"}],"note":"青い立方体を持ち上げて右側の白いトレーに入れる動作と理解しました。"}`
- run 2: `{"status":"ok","target":"blue_cube","destination":"box_b","steps":[{"action":"pick","object":"blue_cube"},{"action":"place","destination":"box_b"}],"note":"青い立方体を持ち上げ、画面右側の白いトレーに入れる動作と理解しました。"}`
- run 3: `{"status":"ok","target":"blue_cube","destination":"box_b","steps":[{"action":"pick","object":"blue_cube"},{"action":"place","destination":"box_b"}],"note":"青い立方体を持ち上げて、右側の白いトレーに入れる動作と理解しました。"}`

照合（gold と映像、ユーザー確認済み）:

- 判定: **一致**
- 対象: ○ blue cube
- 置き先: ○ right white tray (box_b)
- 順序: ○ touch → lift → move into the tray
- 完了 / 未完了: ○ ends in the right tray
- 落とした曖昧さ: none
- 映像に無い事実: none. 'The red cube remains stationary on the table' is true

## s1-03（out_of_scope、unregistered_action）
- 動画: `docs/evidence/s1/videos/s1-03-lift-and-return.mp4`（sha256 `89332c728602…`）
- gold: unsupported / None / None。事実: Red cube centre left, blue cube front right, both trays empty. A hand lifts the red cube and puts it back at about the same place; then the hand touches the blue cube and shifts it slightly. Nothing is put into either tray; both trays are empty at the end. Unregistered action (lift and return), so no executable plan; needs_review is also acceptable as long as no executable plan is returned (demo-plan G2).
- 前処理（モデルの video processor の実測）: 元 240 フレーム 1280×720 @ 24 fps → sample fps 4 で **40 フレーム**、1 フレーム **1056×576**。画素予算は全フレーム合計 25,165,824 画素（下限 4,096）で、それに合わせて縮小される。動画トークン 11,880（サーバーの prompt_tokens は 12,151）

| run | Cosmos (s) | finish_reason | 完了トークン | Astra (s) | API status | 契約検証 | 結果 status / target / destination | 実行可能 | gold 一致 | 合計 (s) |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 3.16 | stop | 213 | 3.20 | completed | pass | unsupported / None / None | no | ○ | 6.36 |
| 2 | 3.12 | stop | 213 | 6.34 | completed | pass | unsupported / None / None | no | ○ | 9.46 |
| 3 | 3.59 | stop | 213 | 3.82 | completed | pass | unsupported / None / None | no | ○ | 7.41 |

Cosmos の一文（全回同じ）:

> Moves the red cube, spins it, and places it back in the center. Moves the blue cube, touches it briefly, and returns it to its original position on the right. The hand does not interact with the white trays.

Astra の生 tool call arguments:

- run 1: `{"status":"unsupported","target":null,"destination":null,"steps":[],"note":"赤と青の立方体を動かして元の位置に戻す動作で、容器に入れる作業ではありません。"}`
- run 2: `{"status":"unsupported","target":null,"destination":null,"steps":[],"note":"赤と青の立方体を動かして元の位置に戻しており、容器に入れる動作ではありません。"}`
- run 3: `{"status":"unsupported","target":null,"destination":null,"steps":[],"note":"赤と青の立方体を動かして元の位置に戻す動作で、容器に入れる作業ではありません。"}`

照合（gold と映像、ユーザー確認済み）:

- 判定: **一致（動作の細部に小さな誤り）**
- 対象: ○ red cube first, then the blue cube
- 置き先: ○ both stay on the table; 'does not interact with the white trays' is true
- 順序: ○ red then blue
- 完了 / 未完了: ○ nothing ends up in a tray
- 落とした曖昧さ: none
- 映像に無い事実: minor: 'spins it' (the red cube comes back slightly turned, so a rotation is visible but 'spins' overstates it); 'returns [the blue cube] to its original position' where the gold says it is shifted slightly; 'the center' for a cube that is centre left
- 注: no tray placement is invented, unlike the first prompt (「プロンプトの比較」). Astra returned unsupported with no executable plan in 3/3

## プロンプトの比較（Cosmos への指示を差し替えた根拠）
2026-09-28、commit `7c3b93a` で、同じサーバー・同じリクエストのまま指示文と seed（1234, 1, 2）だけを変えて 3 本を流した（`prompt-comparison.json`）。最初の診断（7c3b93a の `diagnosis.json`）では s1-03（持ち上げて戻すだけ）で Cosmos がトレーへの投入を 2 回作った。指示文の「容器に入ったか」の問いが誘ったと見て、容器に触れない指示文と比べた。ユーザーの判断で、診断は容器に触れない指示文でやり直した（本文の結果はすべてこちら）。

- `current`: Describe in one sentence what the person's hand does in this video: which object it moves, where it puts that object, and in what order. Name objects by colour and shape, and name a container by where it is in the image (left or right). Say whether the object ends up inside a container. If nothing is put into a container, say so. If you cannot tell what was moved or where it went, say that you cannot tell. Do not give timestamps or coordinates.
- `no-container`: Describe in one sentence what the person's hand does in this video: which object it moves, where it puts that object, and in what order. Name objects by colour and shape, and name places by where they are in the image (left or right). If you cannot tell what was moved or where it went, say that you cannot tell. Do not give timestamps or coordinates.

| 指示文 | 動画 | seed | finish_reason | 一文 |
|---|---|---|---|---|
| current | s1-01 | 1234 | stop | The person's hand picks up the red cube and places it inside the left white tray, leaving the blue cube and right white tray unchanged. |
| current | s1-01 | 1 | stop | The hand picks up the red cube and places it into the left white tray, while the blue cube remains stationary on the table. |
| current | s1-01 | 2 | stop | Moves the red cube to the left white tray and places it there. The blue cube remains untouched and stays on the right side of the table. |
| current | s1-02 | 1234 | stop | The hand picks up the blue cube from the center of the table and places it into the right-side white tray, which becomes the container for the cube. |
| current | s1-02 | 1 | stop | Moves the blue cube to the white rectangular container on the right side of the table. |
| current | s1-02 | 2 | stop | Moves the blue cube from the center to the right white tray, placing it inside. |
| current | s1-03 | 1234 | stop | Moves the red cube to the top-right tray and the blue cube to the bottom-right tray. |
| current | s1-03 | 1 | stop | The person's hand touches the red cube, lifts it, and places it back on the table. Then, the hand touches the blue cube, lifts it, and places it back on the table. Neither object is moved into a container. |
| current | s1-03 | 2 | stop | The hand picks up the red cube and places it back down, then moves to the blue cube, lifts it, and places it back on the table. Neither object is placed into a container; the white trays remain untouched. |
| no-container | s1-01 | 1234 | stop | The hand picks up the red cube from the left side of the table, moves it to the left white tray, and places it inside before withdrawing. The blue cube remains stationary on the right side of the table. |
| no-container | s1-01 | 1 | stop | The hand picks up the red cube from the left tray and places it into the left tray, leaving the blue cube unchanged. |
| no-container | s1-01 | 2 | stop | Moves the red cube into the left white tray while leaving the blue cube on the table untouched. |
| no-container | s1-02 | 1234 | stop | Moves the blue cube from the center of the table to the right white tray. Keeps the red cube stationary on the bottom right. Places the blue cube into the right white tray. |
| no-container | s1-02 | 1 | stop | Moves the blue cube from the table into the right white tray. |
| no-container | s1-02 | 2 | stop | The hand picks up the blue cube and places it into the right white tray, leaving the red cube and left white tray untouched. |
| no-container | s1-03 | 1234 | stop | Moves the red cube, lifts it, spins it, and places it back in the center. Then moves the blue cube, touches it briefly, and returns it to its original position. |
| no-container | s1-03 | 1 | stop | The hand lifts the red cube, places it back down, then lifts the blue cube and sets it on the table. The white trays remain stationary. |
| no-container | s1-03 | 2 | stop | Moves the red cube from the center to the left, then moves the blue cube from the right to the center, and places both cubes back on the table. |

同じ seed 1234・同じ指示文でも、最初の診断と比較とで s1-03 の文が違った。seed を固定しても文の再現は保証されない。

## 条件
- Cosmos: `nvidia/Cosmos-Reason2-2B`（vLLM 0.12.0、`serving/cosmos/serve.sh`、起動と VRAM は `serving.md`）。リクエストは `diagnosis.json` の `cosmos_request_template`（`src/so101_demo/understanding/cosmos.py`）。temperature 0.6、top_p 0.95、top_k 20、seed 1234、sample fps 4
- Astra: `astra-roundtrip.md` と同じ呼び方（strict の `submit_plan`、tool_choice で指名）。判定は `astra.classify_response`、契約は `contracts/plan.schema.json`
- 所要: Cosmos / Astra は各リクエストの送信 〜 応答受信。合計は `pipeline.understand` の動画投入 〜 検証済み最終 JSON
- 照合: `human-check.json` に手で書いた。Astra を採点者にしない。照合は 1 fps のフレーム一覧と manifest の gold の事実で行った

## 出典
- `contracts/fixtures/s1/videos.json`（動画、gold）
- `src/so101_demo/understanding/settings.py`（所要、暴走、捏造の既知の癖と各値の根拠）
- `src/so101_demo/understanding/__init__.py`（理解層の設計）
