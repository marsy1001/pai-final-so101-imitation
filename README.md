# pai-final-so101-imitation

松尾研「Physical AI 応用編 1 知能ロボティクス講座（2026 Summer）」最終課題の提出リポジトリ。

**人が机の上でキューブを箱に片付ける約 10 秒の動画を AI が読み取り、人が承認すると MuJoCo 上の SO-101 が同じ作業を再現する。**

1. **録画**: 人が赤 / 青のキューブを箱 A / B のどちらかへ移す（2×2 の 4 組合せ）
2. **理解**: Cosmos Reason 2（ローカル vLLM）が動画を英語の一文にし、GPT-6 Astra がその一文だけから契約 JSON（`status` / `target` / `destination` / `steps`）を作る。登録外の物体や動作は `unsupported`、判断できないものは `needs_review` として返す。`ok` 以外と判定されたプランは schema 上 `steps` を持てず、実行に流れ込まない（動画を誤って `ok` と読んだ場合は次の承認で人が止める）
3. **承認**: 人がブラウザの承認画面で日本語の要約と対象・置き先を確認し、「これで動かす」を押す。「対応外にする」で止められる。承認前にロボットは動かない
4. **実行**: 承認済みの JSON を、IK で関節角に変換した pick / place で MuJoCo 上の SO-101 が実行する。物体位置は sim の真値。把持は簡略把持（閉じたとき顎の間に登録物体が 1 個だけあれば保持）

真似るのは「何をどこへ」という手順と結果であり、手の軌道や物体配置ではない。

## 構成
```
src/so101_demo/
  kinematics/     placo による FK / IK、上面からの手先目標、作業領域の拒否判定
  sim/            MuJoCo のシーン（SO-101 + 机 + キューブ 2 + 箱 2）、簡略把持、描画
  robot/          RobotBackend（関節指令・グリッパー・状態・停止）と MuJoCo 実装
  locate/         物体 ID -> 位置（sim の真値）
  plan/           契約 JSON のスキーマ検証（contracts/plan.schema.json, registry.json）、固定試行の manifest
  execution/      pick / place プリミティブ、安全層、判定器、plan runner
  understanding/  Cosmos クライアント（一文）、Astra クライアント（strict function calling）、パイプライン
  ui/             承認画面と大画面（FastAPI + 静的 HTML/JS）、通し実行の記録と検査（evidence_e2e）
  evidence/       評価スクリプト（uc_eval: 理解層の評価、s1_diagnosis: 理解層の診断、stage1_g1: 固定試行、place_margin: 置き余裕）
  vendor/         LeRobot RobotKinematics（THIRD_PARTY_NOTICES.md）
contracts/        契約 JSON スキーマ、登録物体、サンプル（ok / unsupported / needs_review / error）、評価用 manifest と固定 plan
serving/cosmos/   Cosmos Reason 2 2B を vLLM で起動する別 uv プロジェクト
docs/             brief（目的・要件）、レポート原稿、コードの説明、評価記録（docs/evidence/）、ADR
docs/evidence/    uc/ 理解層の評価 15 本、e2e/ 通し実行の記録と録画、s1/ 理解層の診断
tests/            pytest（GPU・vLLM・表示が要るものは `gpu` マークで既定スキップ）
```

## 動かし方
```bash
scripts/fetch-so101.sh                  # SO-101 モデル（TheRobotStudio/SO-ARM100 eecbe3e）を assets/so101/ に取得
uv sync
uv run pytest                           # 物理・IK・契約・runner・UI のテスト（GPU 不要）

# 理解層（GPU 機）: 別ターミナルで Cosmos を起動し、.env に Azure OpenAI の接続先を書く（.env.example）
serving/cosmos/serve.sh
uv run python -m so101_demo.evidence.uc_eval --run --revision 2   # 評価動画 15 本を通し、docs/evidence/uc/ に記録

# 通し 1 コマンド（GPU 機、描画あり）: 動画の選択 → 読み取り → 承認 → sim 実行 → 録画と記録
MUJOCO_GL=egl uv run python -m so101_demo.ui --videos contracts/fixtures/uc/videos.json --ids uc-02,uc-05,uc-08,uc-10,uc-13 --record docs/evidence/e2e
#   承認画面 http://127.0.0.1:8765/ 、大画面 http://127.0.0.1:8765/big
uv run python -m so101_demo.ui.evidence_e2e --check docs/evidence/e2e/runs.jsonl   # 記録の検査
uv run python -m so101_demo.ui.evidence_e2e --report docs/evidence/e2e/runs.jsonl > docs/evidence/e2e/runs.md  # 記録から runs.md を再生成

# 理解層なしで画面を動かす（固定 JSON、GPU 不要）
uv run python -m so101_demo.ui

# 固定 10 試行の headless 実行（動画は GPU 機で --video）
uv run python -m so101_demo.evidence.stage1_g1 --approve-fixed-fixture --label 参考
```

固定 revision: MuJoCo 3.13.0、SO-ARM100 `Simulation/SO101` eecbe3e、LeRobot `RobotKinematics` 5aa7455（`src/so101_demo/vendor/`）、Cosmos Reason 2 2B on vLLM 0.12.0。第三者コードのライセンスは `THIRD_PARTY_NOTICES.md`。

## 結果
- 理解層（受け入れ動画 15 本）: 対応内 12 本中 8 本が完全一致、対応外 3 本のうち 1 本を誤受理。`docs/evidence/uc/eval-r2.md`
- 通し実行: 対応内 4 組合せすべてで 承認 → 実行成功、対応外（ペン）は `unsupported` で停止。`docs/evidence/e2e/runs.md`

## 提出物
- 動画（1〜3 分）: YouTube に公開済み
- レポート: `docs/report.md`（`scripts/report-pdf.sh <omnicampus のアカウント名>` で `tmp/` に A4 の PDF を作って提出）
- コード: このリポジトリ

## 開発の進め方
Claude Code（メイン）と Codex（補助レビュー）が agmsg で連携し、GitHub Issue 単位でブランチを切って進める。プロセスの正本は `AGENTS.md`、目的とスコープは `docs/brief.md`、エージェント環境の準備は `docs/agent-workflow.md`。
