# 承認画面と表示画面の起動

- 作成: 2026-09-29（旧プロジェクト）。2026-10-27 に課題用に書き直した
- 実装: `src/so101_demo/ui/`（`screen.py` 状態遷移、`server.py` HTTP / WebSocket、`sources.py` 理解結果の供給元、`runner_thread.py` 実行スレッド、`static/` HTML / JS）。テスト: `tests/test_ui.py`

## 結論
`uv run python -m so101_demo.ui` で起動する。ブラウザで次の 2 つを開く（WSL2 の localhost は Windows 側のブラウザから開ける）。

- 承認画面（操作する人向け）: http://localhost:8765/
- 表示画面（動画に映す側。ロボットの状態と、`--render` 付きなら sim の映像）: http://localhost:8765/big

承認画面は、理解層が動画から読み取った結果を人が確認してからロボットを動かすための関所である。「これで動かす」を押すまで何も動かず、対応外の動画は「対応外にする」で動かさないまま終われる。

**今の状態（2026-10-27）**: 「読み取り開始」が返すのは開発用の固定 JSON（`FixedSource`、`contracts/fixtures/4a/` を順番に返す）だけで、本物の理解層（`understanding.pipeline.understand`）は画面にまだ繋がっていない。接続は通し 1 コマンドの Issue で行う。`sources.py` の `start(run_id, deliver)` が差し替え点。

## 起動
```bash
uv run python -m so101_demo.ui                  # 描画なし（表示画面は状態表示だけ）
uv run python -m so101_demo.ui --render         # 表示画面に sim を描画（styled シーン、1280x720 JPEG）
uv run python -m so101_demo.ui --port 8765 --delay 1.0
```
- `--delay`: 固定 JSON の供給元が、読み取り開始から結果を返すまでに待つ秒数（開発用）
- `--render` は `MUJOCO_GL=egl` と WSL2 の D3D12 選択（`evidence/gl_env.py`）を自動で設定する。runner と描画は専用スレッド 1 本で動く（`ui/runner_thread.py`）
- Cosmos（vLLM）と同じ GPU で描画するときは、ブラウザのハードウェアアクセラレーションを切ると GPU メモリに余裕ができる

## 操作
| 状態 | 押せるボタン |
| --- | --- |
| 待機 | 読み取り開始、手動で選ぶ、事前録画 |
| 読み取り中 | 手動で選ぶ、事前録画（待機上限 `RUN_TIMEOUT_S` を超えると 拒否 / 失敗 へ） |
| 承認待ち | これで動かす（承認）、訂正する、対応外にする、手動で選ぶ |
| 拒否 / 失敗 | 訂正する（結果が画面にあるときだけ。待機上限切れでは出ない）、対応外にする、手動で選ぶ、待機へ戻る |
| 手動 | 候補（対象 × 置き先）を選んで動かす、待機へ戻る、事前録画 |
| 実行中 | なし（実行中のプラン＝訂正・手動の後はその内容を表示。完了すると待機へ戻る） |
| 事前録画 | 待機へ戻る（再生そのものは範囲外） |

- 承認・訂正・手動で選んだプランは、契約で再検証してから `approve_from_review` を通って runner に渡る（`docs/execution-runner.md`）
- 手動・対応外・待機へ戻る を押すと、読み取り中の run は取り消される。後から届いた結果は画面に出ず、実行も起こさない（`understanding/runs.py` の `RunGate`）
- 「読み取った内容」には、プランの `note`（日本語の一文の要約）を承認待ち・拒否 / 失敗・手動で出す。Cosmos の英語の一文は画面に出さない（2026-09-29 の決定）
- 対象・置き先は色と登録ラベルで出す（例: 赤いキューブ R、箱 A。`contracts/registry.json` の `label`）
- 操作は 1 操作 1 行で `ApprovalScreen.log` に残る。訂正と手動は `rescued: true` で、自動理解の成功には数えない

## API（画面が使うもの）
`GET /api/view`、`WS /ws`（表示内容の push）、`GET /frame.jpg`、`POST /api/{read,approve,correct,reject,manual,choose,prerecorded,idle}`。今の状態で許されない操作は 409 を返し、何も変えない。`approve` / `correct` / `reject` / `choose` は、ページが表示していた view の `session`（読み取りの run ID、または手動の番号）を必ず送る。表示中のものと違う（古い画面や遅れたリクエスト）と 409 になる。供給元が起動できなかった `read` は 503 を返し、待機へ戻る。

## 証拠の取り方
- 固定 JSON での 4 経路（承認・訂正・拒否・手動）を headless で通す: `uv run python -m so101_demo.ui.evidence_3b` → `docs/evidence/3b/operations.md` + `operations.json`（laptop では参考）
- 本物の理解結果での操作記録と 1920×1080 の画面キャプチャは、通しの Issue と動画の Issue で撮る

## 参照
- `src/so101_demo/ui/`、`tests/test_ui.py`
- `docs/execution-runner.md`（承認マークと実行器の入口）
