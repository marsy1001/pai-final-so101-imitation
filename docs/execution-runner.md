# 実行器（PlanRunner）: 契約 JSON から sim への唯一の入口

- 作成: 2026-09-19（Issue #9、demo-plan 第 1 段 4a 実行側）
- 実装: `src/so101_demo/execution/runner.py`。証拠: `docs/evidence/4a/runs.md`。テスト: `tests/test_runner.py`

## 結論
契約 JSON（`contracts/plan.schema.json`）を実行に変える入口は `PlanRunner.submit` の 1 つで、受け取るのは承認マーク付きのプラン（`ApprovedPlan`）だけである。承認マークを作る経路は次の 2 つしかなく、第 3 の経路は無い。

| 経路 | 関数 | 用途 | 本番での扱い |
|---|---|---|---|
| 開発用固定 fixture | `approve_for_development(plan, fixture_id, approve_fixed_fixture=True)` | `contracts/fixtures/4a/`（G1 では `contracts/fixtures/g1/`）を CLI から流す。CLI `so101_demo.evidence.stage1_4a` は `--approve-fixed-fixture` を付けないと何も実行せず終了コード 2 | 使わない。承認画面ができた後は開発・評価専用。ログに `approval_source = dev-fixed-fixture` が残るので本番記録と混ざらない |
| 承認画面 | `approve_from_review(plan, run_id)` | 第 2 段 3b の承認画面が、表示・再検証したプランを人の操作で承認する | 本番の唯一の経路（3b で実装） |

承認マーク無しの入力（JSON 文字列・dict・検証済み `Plan`）や不正な JSON は `rejected` として記録され、RobotBackend には関節・グリッパー指令が 1 つも送られない（`commands_sent = 0`）。

承認済みのプランも信用しない。`Plan` は素のデータクラスで手で組める（`dataclasses.replace` など）ので、承認関数（`ApprovedPlan.__post_init__`）と、実行器が最初の指令を送る直前の 2 か所で `recheck_plan`（契約 JSON への往復検証: スキーマ・登録 ID・target と steps の整合）を通す。違反は `rejected`（`invalid_plan`）、指令 0（2026-09-19 レビュー round 1 指摘 1）。

## 状態遷移
```
待機 ──submit(承認済み)──▶ 実行中 ──判定──▶ 完了 ──▶ リセット ──▶ 待機
  │                           │
  │                           ├──安全層の拒否（範囲外など。動作前に判明）──▶ 完了(rejected)
  │                           ├──stop()──▶ 完了(stopped)
  │                           └──想定外の例外──▶ 完了(error)          いずれも → リセット → 待機
  └──submit(未承認・不正 JSON)──▶ 完了(rejected) ──▶ リセット ──▶ 待機   （実行中を経ない。動作なし）
実行中 ──submit()──▶ busy を返す（遷移を記録しない。実行中のプランはそのまま）
```
実行後のリセット判定が通らなければ、pick / place が判定器で success でも総合結果は `reset_failed` で成功に数えない（`criteria.py`「A run whose reset check fails is not a pass」。レビュー round 1 指摘 3）。

観測（動画の recorder = `observer`）はプラン経路の外にある。実行中に observer が例外を出したら、その実行の `observer_error` に最初の 1 件を記録して以後その実行では呼ばず、物理と判定は続ける。リセット中は observer もフックも呼ばない（物理だけ）ので、recorder が壊れ続けてもリセットは完了し `busy` に固着しない（レビュー round 1 指摘 2）。動画は 実行中 の区間だけを含む。

各遷移は sim 時刻と壁時計（UTC、ISO 8601）付きで `RunRecord.transitions` に残る。1 回の submit の記録に含まれる遷移は、承認済みプランを受け付けた場合 実行中 → 完了 → リセット → 待機 の 4 つ（安全層の拒否・停止・error も同じ 4 つ。`commands_sent` で動作の有無を見る）、動作前の拒否では 完了 → リセット → 待機 の 3 つ、`busy` では 0。想定外の例外（フックの失敗など。録画 = observer の失敗は上のとおり `observer_error` に分離し、物理結果には影響しない）も握りつぶさず `error` として記録し、リセットして 待機 に戻る。

## リセット完了の定義（Issue #9 で 2026-09-19 に固定。`execution/criteria.py` の `RESET_*`）
`backend.reset()`（`mj_resetData`、ホーム姿勢、開、weld 全解除）→ 登録物体すべてを `registry.json` の `home_xyz_m` に配置 → `RESET_SETTLE_S` = 1.0 s 静定 → 次を全部満たす。

| 項目 | 値 |
|---|---|
| 物体の位置差（home から） | ≤ 1 mm（`RESET_OBJECT_POS_TOL_M`） |
| 物体の傾き | ≤ 1°（`RESET_OBJECT_TILT_MAX_DEG`） |
| 関節（ホーム姿勢から、各軸） | ≤ 1°（`RESET_JOINT_TOL_DEG`） |
| グリッパー | ≥ 60（`GRIPPER_OPEN_MIN`、0b と共通） |
| 保持 | なし（`SimpleGrasp.held is None`） |
| equality 拘束 | 有効 0 本 |
| 初期干渉 | 1 mm 超の貫入なし |

判定はリセットのたび（実行器の起動時と、毎回の submit の後）に行う。`RunRecord` には直前のリセットの結果（`reset_previous`）と実行後のリセットの結果（`reset_after`）を残し、直前のリセットが通っていなければもう一度リセットしてから実行し、それでも通らなければ実行せず `rejected` にする。submit 時点で物体が home にあることは要求しない（G1 では home 以外から始める試行がある）。代わりに submit 時の全物体の位置を `scene_at_submit` に記録する。

## 判定
pick / place の成否は 0b と同じ `execution.judge`（`execution.criteria` の固定値。持ち上げ・搬送監査 A/B/C・置き先・静止・時間上限）で、物体 pose とグリッパー値だけから判定する。保持リストは判定に使わない。加えて `RunRecord` に次を残す。

- `commands_sent`: その submit で RobotBackend が受けた関節・グリッパー・stop 指令の数（`busy` は 0）
- `scene_at_submit`: submit 時の全物体の位置
- `other_objects_max_displacement_m`: 対象でない物体が実行中に **submit 時の位置**（`scene_at_submit`。4a では home、G1 では manifest の開始位置）から動いた最大距離（期待値 0）
- `grasp_events`: 閉・開指令ごとの hold / no_hold / release と候補（診断用）
- `trace`: プリミティブの節目（`pick:pre_grasp` … `place:retreated`、`stop`）

## シーン
登録物体 2 個（red_cube、blue_cube）と置き先 2 個を同時に置く。開始位置は `registry.json` の `home_xyz_m`（red (0.16, 0.02)、blue (0.22, −0.02)）。Issue 起票時の (0.16, 0) / (0.22, 0) から変えた理由は `docs/evidence/4a/runs.md` 注記（可動顎が径方向 +x に開くため、y = 0 に並べると red の pick で blue に触れる。実測 7.2 mm）。0b の T1〜T5 は `trials_0b.start_xy_m` を使うので影響を受けない。

G1 の固定 10 試行（Issue #19。2026-09-21 に ADR 0008 の配置で作り直した）は、**両物体**を試行ごとの固定位置から始める（home とは限らない）。そのために `PlanRunner.stage(start_xyz)` を追加した: 待機中の実行器で両物体を指定位置に置き、`RESET_SETTLE_S` 静定してからリセットと同じ許容値（位置 1 mm・傾き 1°、関節・開・保持・拘束・貫入）で配置を判定する（「開始配置」判定）。プラン経路からは到達できない。実行後のリセットは従来どおり home へ戻す。

## 作らないもの
承認画面そのもの（第 2 段 3b）、リトライ、通信・API 連携、CameraBackend 等の抽象基盤。G1 の固定 10 試行 manifest は `contracts/fixtures/g1/manifest.json`（`docs/g1-manifest.md`、Issue #19）、合格数字は #13 で扱う。

## 使い方
```
uv run python -m so101_demo.evidence.stage1_4a --approve-fixed-fixture --label 参考
MUJOCO_GL=egl uv run python -m so101_demo.evidence.stage1_4a --approve-fixed-fixture --label 受け入れ --video
```
出力は `docs/evidence/4a/runs.md`（人向け）と `runs.json`（全記録: 遷移・判定・リセット判定・指令数・trace）。

G1 の固定 10 試行:
```
uv run python -m so101_demo.evidence.stage1_g1 --approve-fixed-fixture --label 参考
MUJOCO_GL=egl uv run python -m so101_demo.evidence.stage1_g1 --approve-fixed-fixture --label 受け入れ --video
```
出力は `docs/evidence/g1/trials.md` + `trials.json`（`--video` で `T<nn>-<fixture>-<config>.mp4`、Issue #10 の venue recorder）。
