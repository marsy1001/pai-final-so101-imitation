# 承認画面と表示画面の起動

- 作成: 2026-09-29（旧プロジェクト）。2026-10-27 に課題用に書き直した
- 実装: `src/so101_demo/ui/`（`screen.py` 状態遷移、`server.py` HTTP / WebSocket、`sources.py` 理解結果の供給元（固定 JSON / 動画）、`runner_thread.py` 実行スレッド、`recording.py` 表示用フレームと録画、`runs_log.py` 1 run 1 行の記録、`evidence_e2e.py` 記録の検査と文書化、`static/` HTML / JS）。テスト: `tests/test_ui.py`、`tests/test_ui_e2e.py`

## 結論
`uv run python -m so101_demo.ui` で起動する。ブラウザで次の 2 つを開く（WSL2 の localhost は Windows 側のブラウザから開ける）。

- 承認画面（操作する人向け）: http://localhost:8765/
- 表示画面（動画に映す側。ロボットの状態と、`--render` 付きなら sim の映像）: http://localhost:8765/big

承認画面は、理解層が動画から読み取った結果を人が確認してからロボットを動かすための関所である。「これで動かす」を押すまで何も動かず、対応外の動画は「対応外にする」で動かさないまま終われる。

**今の状態（2026-10-27、Issue #3）**: 「読み取り開始」は、`--videos` を付けて起動すると次の動画を本物の理解層（`understanding.pipeline.understand`: Cosmos の一文 → Astra の契約 JSON）に送り、結果を承認待ち / 拒否 / 失敗として画面に出す（`sources.py` の `VideoSource`）。`--videos` 無しでは従来どおり開発用の固定 JSON（`FixedSource`、`contracts/fixtures/4a/` を順番に返す）。`--record <dir>` を付けると、承認された実行を 1920×1080・25 fps の mp4 に録画し、1 run 1 行の記録を `<dir>/runs.jsonl` に残す。通しの記録は `docs/evidence/e2e/runs.md`。

## 起動
```bash
uv run python -m so101_demo.ui                  # 描画なし、固定 JSON（表示画面は状態表示だけ）
uv run python -m so101_demo.ui --render         # 表示画面に sim を描画（styled シーン、1280x720 JPEG）
uv run python -m so101_demo.ui --port 8765 --delay 1.0

# 通し（GPU 機）: 別ターミナルで serving/cosmos/serve.sh を起動し、.env に Astra の接続先を書いておく
MUJOCO_GL=egl uv run python -m so101_demo.ui --videos contracts/fixtures/uc/videos.json \
  --ids uc-02,uc-05,uc-08,uc-10,uc-13 --record docs/evidence/e2e
```
- `--videos <manifest|dir>`: 読み取る動画。manifest（`contracts/fixtures/uc/videos.json` の形: `videos[].id` と `path`）か、mp4 を置いたディレクトリ（id はファイル名）。「読み取り開始」のたびに次の動画を順に（末尾で先頭に戻る）理解層へ送る。Cosmos サーバー（`serving/cosmos/serve.sh`、`http://localhost:8000`）と `.env` の Astra の接続先（`.env.example`）が要る。どちらも無いときは読み取りが 拒否 / 失敗 になる（Cosmos に繋がらない: `読み取りに失敗しました`。`.env` が無い: 同じ表示で、`runs.jsonl` の `source_error` に理由が残る）
- `--ids a,b,c`: `--videos` のうち使う動画 id と順番（省略時は全部）
- `--record <dir>`: 承認・訂正・手動で実行した run を `<dir>/<run_id>-<target>-<destination>.mp4`（1920×1080、25 fps（sim 時間）、ラベル付き、実行中の区間のみ。`evidence/record.py` の screen recorder、NVENC があれば使う）に録画し、`<dir>/runs.jsonl` に 1 run 1 行を追記する（`ui/runs_log.py`）。拒否した run も 1 行（mp4 なし）。`--render` を含む
- `--delay`: 固定 JSON の供給元が、読み取り開始から結果を返すまでに待つ秒数（開発用）
- `--render` は `MUJOCO_GL=egl` と WSL2 の D3D12 選択（`evidence/gl_env.py`）を自動で設定する。runner と描画・録画は専用スレッド 1 本で動く（`ui/runner_thread.py`、`ui/recording.py`: 1920×1080 の renderer 1 つで、表示画面用の 1280×720 JPEG と録画を同じフレームから作る）
- 読み取りの待機上限は `RUN_TIMEOUT_S`（`understanding/settings.py`、62 秒 = Cosmos 30 + Astra 30 + 2）。理解層の実測（`docs/evidence/uc/eval-r2.md`、2026-09-29: 中央値 19.7 秒、最大 33.2 秒）を超えている（`tests/test_ui_e2e.py`）
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
- 手動・待機へ戻る を押すと、読み取り中の run は取り消される。後から届いた結果は画面に出ず、実行も起こさない（`understanding/runs.py` の `RunGate`）。対応外にする は結果が画面にあるときだけ押せる（読み取り中は 409）
- 読み取り中・承認待ち・拒否 / 失敗・実行中は、読んでいる動画の id を出す（`--videos` のとき。固定 JSON では fixture の id）
- 「読み取った内容」には、プランの `note`（日本語の一文の要約）を承認待ち・拒否 / 失敗・手動で出す。Cosmos の英語の一文は画面に出さない（2026-09-29 の決定）
- 対象・置き先は色と登録ラベルで出す（例: 赤いキューブ R、箱 A。`contracts/registry.json` の `label`）
- 操作は 1 操作 1 行で `ApprovalScreen.log` に残る。訂正と手動は `rescued: true` で、自動理解の成功には数えない

## API（画面が使うもの）
`GET /api/view`、`WS /ws`（表示内容の push）、`GET /frame.jpg`、`POST /api/{read,approve,correct,reject,manual,choose,prerecorded,idle}`。今の状態で許されない操作は 409 を返し、何も変えない。`approve` / `correct` / `reject` / `choose` は、ページが表示していた view の `session`（読み取りの run ID、または手動の番号）を必ず送る。表示中のものと違う（古い画面や遅れたリクエスト）と 409 になる。供給元が起動できなかった `read` は 503 を返し、待機へ戻る。

## 証拠の取り方
- 固定 JSON での 4 経路（承認・訂正・拒否・手動）を headless で通す: `uv run python -m so101_demo.ui.evidence_3b` → `docs/evidence/3b/operations.md` + `operations.json`（laptop では参考）
- 通し（本物の理解結果、録画あり、GPU 機）: 上の `--videos ... --record docs/evidence/e2e` で起動し、画面で操作する。記録の検査と文書化: `uv run python -m so101_demo.ui.evidence_e2e --check docs/evidence/e2e/runs.jsonl`（4 組合せの 承認 + `success`、対応外 1 本以上で指令 0、mp4 が 10 MiB 未満・1920×1080・25 fps）、`--report docs/evidence/e2e/runs.jsonl > docs/evidence/e2e/runs.md`（`runs.jsonl` だけから再現できる）
- 画面キャプチャ（承認画面と表示画面の静止画・画面収録）は動画の Issue で撮る

## 参照
- `src/so101_demo/ui/`、`tests/test_ui.py`、`tests/test_ui_e2e.py`
- `docs/execution-runner.md`（承認マークと実行器の入口）
- `docs/evidence/e2e/runs.md`（通しの記録）
