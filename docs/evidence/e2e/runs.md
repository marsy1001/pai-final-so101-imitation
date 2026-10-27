# 通し実行の記録: 動画 → 理解 → 承認 → sim 実行 → 録画

`uv run python -m so101_demo.ui.evidence_e2e --report docs/evidence/e2e/runs.jsonl` が `runs.jsonl` から生成（1 run 1 行。`python -m so101_demo.ui --videos ... --record` が書く）。

## 結論

- 対応内 4 組合せ: 承認（「これで動かす」）→ runner `success` の run がある組合せ **4/4**（red_cube→box_a: uc-02、red_cube→box_b: uc-05、blue_cube→box_a: uc-08、blue_cube→box_b: uc-10）
- 対応外（manifest `videos.json` の scope が `out_of_scope` の動画）: 「対応外にする」で止めた run **1 本**。uc-13: 理解層は `unsupported`、「対応外にする」で停止、送った指令 0
- 録画: 4 本、1.09 MB 〜 1.19 MB、実効 fps 23.3 〜 24.1（25 fps を割った run: c8bfd9cb6940、da0a5e180853、7fbfb29c59a2、8b3712183216。失敗にはしない）
- 実行日（UTC）: 2026-10-27、commit `b280c93`
- 理解層: Cosmos `nvidia/Cosmos-Reason2-2B`（サーバー報告 `nvidia/Cosmos-Reason2-2B`）、Astra（API 報告のモデル）`gpt-6-astra`
- 構成: `docs/evidence/uc/config-r2.json` の sha256 `2bbbe753c3c1e45b0d04a226535b57cbab772aa42adc6312d1a5895c3be2316d`（実行時に記録）。現在のファイルと**一致**。コードの指示文・要求・上限との差分: なし（Uc 改訂 2 と同じ指示文）
- 読み取りの待機上限 `RUN_TIMEOUT_S` = 62.0 秒

## 条件

| 項目 | 値 |
|---|---|
| 起動 | `MUJOCO_GL=egl uv run python -m so101_demo.ui --videos contracts/fixtures/uc/videos.json --ids uc-02,uc-05,uc-08,uc-10,uc-13 --record docs/evidence/e2e` |
| 端末 | GPU NVIDIA GeForce RTX 4090 Laptop GPU、GL `D3D12 (NVIDIA GeForce RTX 4090 Laptop GPU)`、encoder `h264_nvenc` |
| 録画 | 1920×1080、25 fps（sim 時間）、実行中の区間のみ、ラベル付き（`evidence/record.py`、`evidence/overlay.py`） |
| 承認 | 画面の操作のみ（`approve_from_review`）。自動承認は無い |

## run ごとの結果

| # | run_id | 動画 | 理解層の結果（status / target → destination） | 段の失敗 | 操作 | runner の結果 | 指令数 | 動画ファイル | サイズ | 実効 fps | 所要 s（理解 / 実行） |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `c8bfd9cb6940` | uc-02 | ok / red_cube → box_a | - | 承認 | success | 316 | `c8bfd9cb6940-red_cube-box_a.mp4` | 1.09 MB | 23.5 | 9.35 / 10.46 |
| 2 | `da0a5e180853` | uc-05 | ok / red_cube → box_b | - | 承認 | success | 316 | `da0a5e180853-red_cube-box_b.mp4` | 1.15 MB | 24.1 | 8.71 / 10.22 |
| 3 | `7fbfb29c59a2` | uc-08 | ok / blue_cube → box_a | - | 承認 | success | 316 | `7fbfb29c59a2-blue_cube-box_a.mp4` | 1.16 MB | 23.7 | 10.7 / 10.34 |
| 4 | `8b3712183216` | uc-10 | ok / blue_cube → box_b | - | 承認 | success | 316 | `8b3712183216-blue_cube-box_b.mp4` | 1.19 MB | 23.3 | 8.23 / 10.52 |
| 5 | `67a99d4f4256` | uc-13 | unsupported / - / - | - | 拒否 | not run | 0 | - | - | - | 9.43 / - |

## Cosmos の一文と Astra のプラン

### 1. uc-02（`c8bfd9cb6940`、2026-10-27T08:26:54.830+00:00）

> Moves the red cube from the right side of the table to the white tray on the left, placing it atop the tray.

- プラン: ok / red_cube → box_a、note: 赤い立方体をテーブル右側から左側の白いトレーに移して置く動作です。
- Astra の生 arguments: `{"status":"ok","target":"red_cube","destination":"box_a","steps":[{"action":"pick","object":"red_cube"},{"action":"place","destination":"box_a"}],"note":"赤い立方体をテーブル右側から左側の白いトレーに移して置く動作です。"}`
- 画面に出した案: ok / red_cube → box_a、実行したプラン: ok / red_cube → box_a
- Cosmos 5.37 s（finish `stop`）、Astra 3.96 s

### 2. uc-05（`da0a5e180853`、2026-10-27T08:27:24.538+00:00）

> Moves the red cube from the right side of the table to the white tray on the right edge.

- プラン: ok / red_cube → box_b、note: 赤い立方体を右端の白いトレーに入れる動作と理解しました。
- Astra の生 arguments: `{"status":"ok","target":"red_cube","destination":"box_b","steps":[{"action":"pick","object":"red_cube"},{"action":"place","destination":"box_b"}],"note":"赤い立方体を右端の白いトレーに入れる動作と理解しました。"}`
- 画面に出した案: ok / red_cube → box_b、実行したプラン: ok / red_cube → box_b
- Cosmos 4.74 s（finish `stop`）、Astra 3.97 s

### 3. uc-08（`7fbfb29c59a2`、2026-10-27T08:27:43.930+00:00）

> The hand picks up the blue cube from the left side of the table and places it onto the white tray on the left side of the table.

- プラン: ok / blue_cube → box_a、note: 青い立方体を持ち上げ、左側の白いトレーに置く動作と理解しました。
- Astra の生 arguments: `{"status":"ok","target":"blue_cube","destination":"box_a","steps":[{"action":"pick","object":"blue_cube"},{"action":"place","destination":"box_a"}],"note":"青い立方体を持ち上げ、左側の白いトレーに置く動作と理解しました。"}`
- 画面に出した案: ok / blue_cube → box_a、実行したプラン: ok / blue_cube → box_a
- Cosmos 6.17 s（finish `stop`）、Astra 4.54 s

### 4. uc-10（`8b3712183216`、2026-10-27T08:28:01.356+00:00）

> Moves the blue cube from the right side of the table to the right white tray.

- プラン: ok / blue_cube → box_b、note: 青い立方体をテーブルの右側から右の白いトレーに移す動作と理解しました。
- Astra の生 arguments: `{"status":"ok","target":"blue_cube","destination":"box_b","steps":[{"action":"pick","object":"blue_cube"},{"action":"place","destination":"box_b"}],"note":"青い立方体をテーブルの右側から右の白いトレーに移す動作と理解しました。"}`
- 画面に出した案: ok / blue_cube → box_b、実行したプラン: ok / blue_cube → box_b
- Cosmos 4.8 s（finish `stop`）、Astra 3.43 s

### 5. uc-13（`67a99d4f4256`、2026-10-27T08:28:11.587+00:00）

> The person's hand enters from the bottom right, picks up the pen from the center of the table, and places it into the white tray on the right side.

- プラン: unsupported / - / -、note: ペンを右側の白いトレーに入れていますが、ペンは登録対象外のため実行できません。
- Astra の生 arguments: `{"status":"unsupported","target":null,"destination":null,"steps":[],"note":"ペンを右側の白いトレーに入れていますが、ペンは登録対象外のため実行できません。"}`
- 画面に出した案: unsupported / - / -、実行したプラン: -
- Cosmos 6.24 s（finish `stop`）、Astra 3.19 s

## 出典

- `docs/evidence/e2e/runs.jsonl`（この記録の元。`uv run python -m so101_demo.ui.evidence_e2e --check` が検査する）
- `docs/evidence/uc/eval-r2.md`、`docs/evidence/uc/config-r2.json`（理解層の固定構成と精度）
- `docs/approval-screen.md`（起動と操作）、`docs/execution-runner.md`（承認の経路と判定）
