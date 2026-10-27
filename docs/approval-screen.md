# 承認画面と大画面の起動（3b）

## 結論
`uv run python -m so101_demo.ui` で起動する。Windows 側のブラウザで次の 2 つを全画面で開く。

- 承認画面（タッチディスプレイ）: http://localhost:8765/
- 大画面（1080p）: http://localhost:8765/big

描画を出すときは `--render` を付ける。方式の決定は `docs/adr/0011-browser-ui-for-approval-and-big-screen.md`。

## 起動
```bash
uv run python -m so101_demo.ui                  # 描画なし（大画面は状態表示だけ）
uv run python -m so101_demo.ui --render         # 大画面に sim を描画（styled シーン、1280x720 JPEG）
uv run python -m so101_demo.ui --port 8765 --delay 1.0
```
- `--delay`: 読み取り開始から結果が出るまでの秒数。今の抽出元は 4a の固定 JSON（`contracts/fixtures/4a/`）を順番に返す開発用のもので、実際の理解層（`understanding.pipeline`）への接続は 3c で行う。
- `--render` は `MUJOCO_GL=egl` と WSL2 の D3D12 選択（`evidence/gl_env.py`）を自動で設定する。runner と描画は専用スレッド 1 本で動く（`ui/runner_thread.py`）。
- WSL2 の localhost は Windows 側のブラウザから開ける。
- 本番機ではブラウザのハードウェアアクセラレーションを切る（Cosmos の GPU 独占前提、ADR 0011 の懸念 a）。Cosmos と同時に動かして OOM にならないかは第 3 段「同時常駐」で判定する（有無の比較測定は外した。ADR 0012）。

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

- 承認・訂正・手動で選んだプランは、契約で再検証してから `approve_from_review` を通って runner に渡る。
- 手動・対応外・待機へ戻る を押すと、読み取り中の run は取り消される。後から届いた結果は画面に出ず、実行も起こさない（`RunGate`、#23）。
- 「読み取った内容」には、プランの `note`（日本語の一文、`docs/demo-plan.md` の契約で「画面に出す一文の要約（日本語）」）を承認待ち・拒否 / 失敗・手動で出す。Cosmos の英語の一文は画面に出さない（2026-09-29 のユーザー決定、#27）。
- 対象・置き先は色と登録ラベルで出す（例: 赤いキューブ R、箱 A。`contracts/registry.json` の `label`）。
- 操作は 1 操作 1 行で `ApprovalScreen.log` に残る。訂正と手動は `rescued: true` で、自動理解の成功には数えない。

## API（画面が使うもの）
`GET /api/view`、`WS /ws`（表示内容の push）、`GET /frame.jpg`、`POST /api/{read,approve,correct,reject,manual,choose,prerecorded,idle}`。今の状態で許されない操作は 409 を返し、何も変えない。`approve` / `correct` / `reject` / `choose` は、ページが表示していた view の `session`（読み取りの run ID、または手動の番号）を必ず送る。表示中のものと違う（古い画面や遅れたリクエスト）と 409 になる。抽出元が起動できなかった `read` は 503 を返し、待機へ戻る。

## 証拠
- 固定 JSON での 4 経路（XPS、参考）: `uv run python -m so101_demo.ui.evidence_3b` → `docs/evidence/3b/operations.md`
- 実際の抽出結果（#25 の Uc 改訂 2）での 4 経路と 1920×1080 のキャプチャ（母艦）: `uv run python -m so101_demo.ui.evidence_3b_gpu` → `docs/evidence/3b/operations-gpu.md`、`docs/evidence/3b/captures/`。キャプチャは Windows 側の Edge を headless・GPU 無効で使う

## 出典
- `docs/demo-plan.md` 第 2 段の表「3b 承認画面」、「見栄えの最低ライン」の状態表示、「通信方針」
- 4 経路の操作記録: `docs/evidence/3b/operations.md`
