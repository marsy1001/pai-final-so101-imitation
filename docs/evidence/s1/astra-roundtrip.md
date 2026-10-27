# S1: Astra 往復の実測（人が書いた文 → submit_plan → 契約 schema 検証）

`uv run python -m so101_demo.evidence.s1_diagnosis --astra-roundtrip` が生成（Issue #22 XPS 分）。

## 結論
- 実行日 2026-09-28、commit `60c8c2b`、モデル（API の報告値）: gpt-6-astra
- このアカウントの Astra デプロイで、strict の `submit_plan` を tool_choice で指名した Responses API 呼び出しが **7/7 件 completed**、契約 schema（`contracts/plan.schema.json` の完全版）の検証を **7/7 件** 通った
- 事前に書いた期待（status・target・destination）との一致: **6/7**。期待が ok でない文から実行可能プランが返った件数: **0**
- 不一致: A5（期待 needs_review / blue_cube / None → unsupported / None / None、実行不可）。本 Issue ではプロンプトを直さない（作り込みと 1 回の直しは Uc）
- 所要（リクエスト送信 〜 応答受信）: 中央値 4.7 秒、最大 5.7 秒（待機上限の暫定値 30 秒）
- これは文 → JSON の段だけの測定で、文は人が書いた。動画からの通し（Cosmos → Astra）は母艦分。3 種 7 文の結果を品質の受け入れと同一視しない

## 各文の結果
| id | 種別 | 期待 | 結果 status / target / destination | 一致 | 実行可能 | API status | 契約検証 | 所要 (s) |
|---|---|---|---|---|---|---|---|---|
| A1 | 正しい文 | ok / red_cube / box_a | ok / red_cube / box_a | ○ | yes | completed | pass | 4.7 |
| A2 | 正しい文 | ok / blue_cube / box_b | ok / blue_cube / box_b | ○ | yes | completed | pass | 5.7 |
| A3 | 対応外（未登録物体） | unsupported / None / None | unsupported / None / None | ○ | no | completed | pass | 4.5 |
| A4 | 対応外（未登録動作） | unsupported / None / None | unsupported / None / None | ○ | no | completed | pass | 4.9 |
| A5 | 置き先が欠けた文 | needs_review / blue_cube / None | unsupported / None / None | × | no | completed | pass | 5.3 |
| A6 | 対象が欠けた文 | needs_review / None / box_b | needs_review / None / box_b | ○ | no | completed | pass | 3.6 |
| A7 | 曖昧な文 | needs_review / None / None | needs_review / None / None | ○ | no | completed | pass | 4.5 |

## 送った文と生の tool call arguments

### A1 正しい文
- 文: A hand picks up the red cube and puts it into the tray on the left side of the image.
- response id: `resp_00ff7022989e73c5016ab9e77aa50c8195b33744551ccb19d9`、usage: input 611 / output 78 tokens
- arguments:

```json
{"status":"ok","target":"red_cube","destination":"box_a","steps":[{"action":"pick","object":"red_cube"},{"action":"place","destination":"box_a"}],"note":"赤い立方体を持ち上げ、画像の左側のトレーに入れる動作です。"}
```

### A2 正しい文
- 文: The person grabs the blue cube, carries it across the table and drops it into the container on the right.
- response id: `resp_098d72cd5331106a016ab9e77ed1ac8190b4712694951c5464`、usage: input 612 / output 81 tokens
- arguments:

```json
{"status":"ok","target":"blue_cube","destination":"box_b","steps":[{"action":"pick","object":"blue_cube"},{"action":"place","destination":"box_b"}],"note":"青い立方体を持ち上げて、画面右側の容器に入れる動作と理解しました。"}
```

### A3 対応外（未登録物体）
- 文: A hand puts a black pen into the tray on the left side of the image.
- response id: `resp_034c6525ee48cc31016ab9e784090c8193a85a6cb0b5f39542`、usage: input 607 / output 56 tokens
- arguments:

```json
{"status":"unsupported","target":null,"destination":null,"steps":[],"note":"黒いペンを左のトレーに入れる動作ですが、ペンは登録対象外です。"}
```

### A4 対応外（未登録動作）
- 文: A hand lifts the red cube and puts it back where it was; nothing is put into a tray.
- response id: `resp_08604b4570f1122a016ab9e7885a7081909df5d9e4d335d5fa`、usage: input 611 / output 67 tokens
- arguments:

```json
{"status":"unsupported","target":null,"destination":null,"steps":[],"note":"赤い立方体を持ち上げて元の場所に戻す動作は、容器に入れる作業ではないため対象外です。"}
```

### A5 置き先が欠けた文
- 文: A hand picks up the blue cube and carries it out of view.
- response id: `resp_003ea9befb59cc35016ab9e78cb67481938039c97e6aa4e660`、usage: input 604 / output 83 tokens
- arguments:

```json
{"status":"unsupported","target":null,"destination":null,"steps":[],"note":"青い立方体を持ち上げて画面外へ運ぶ動作は、容器に入れる対応タスクではありません。"}
```

### A6 対象が欠けた文
- 文: A hand puts something into the tray on the right, but the object is hidden by the hand.
- response id: `resp_0f1da3d308a26ae8016ab9e791881c8195af8438772d6d4272`、usage: input 610 / output 60 tokens
- arguments:

```json
{"status":"needs_review","target":null,"destination":"box_b","steps":[],"note":"右側のトレーに物を入れていますが、手で隠れていて対象を特定できません。"}
```

### A7 曖昧な文
- 文: A hand puts a cube into one of the trays.
- response id: `resp_0be46b75ff1f952b016ab9e794f2908197bdba8219a2d54b26`、usage: input 601 / output 61 tokens
- arguments:

```json
{"status":"needs_review","target":null,"destination":null,"steps":[],"note":"立方体をトレーに入れる動作ですが、立方体の色とトレーの左右が不明です。"}
```

## 条件
- 接続: Azure OpenAI の v1 API `POST {AOAI_ENDPOINT}/openai/v1/responses`、`model` = `AOAI_ASTRA_DEPLOYMENT`。キーとエンドポイントは `.env`（リポジトリ外）
- リクエスト: `instructions`（登録 ID と status の規則）+ `input`（`Sentence: <文>`）、`tools` = strict の `submit_plan` 1 つ、`tool_choice` で `submit_plan` を指名、`parallel_tool_calls: false`、`store: false`。全文は `astra-roundtrip.json` の `request_template`
- 判定: `src/so101_demo/understanding/astra.py` の `classify_response`。completed で `submit_plan` がちょうど 1 回、引数が契約 schema を通ったものだけを plan とし、それ以外は `status: error`（実行不可）
- 期待値は実行前に `src/so101_demo/evidence/s1_diagnosis.py` の `ROUNDTRIP_SENTENCES` に書いた。Astra を採点者にしない

## 出典
- `docs/handover/understanding-layer-notes.md` §3（strict・tool_choice・parallel_tool_calls・refusal / incomplete の扱い）、§5（Astra 単体評価の文の種類）
- `docs/demo-plan.md`「理解層の設計」
