# pai-final-so101-imitation

松尾研「Physical AI 応用編 1 知能ロボティクス講座（2026 Summer）」最終課題の提出リポジトリ。

**人が机の上でキューブを箱に片付ける約 10 秒の動画を AI が読み取り、MuJoCo 上の SO-101 が同じ作業を再現する。**

1. **録画**: 人が赤 / 青のキューブを箱 A / B のどちらかへ移す（2×2 の 4 組合せ）
2. **理解**: Cosmos Reason 2（ローカル vLLM）が動画を一文にし、GPT-6 Astra が契約 JSON（`status` / `target` / `destination` / `steps`）に構造化する。登録外の物体や動作は `unsupported`、判断できない動画は `needs_review` として返す
3. **承認**: 人がブラウザの承認画面で一文と JSON を確認し、「合っている」を押す。承認前にロボットは動かない
4. **実行**: 承認済みの JSON を、IK で関節角に変換した pick / place プリミティブが MuJoCo 上の SO-101 で実行する。物体位置は sim の真値、把持は簡略把持（触れたら保持）

真似るのは「何をどこへ」という手順と結果であり、手の軌道や物体配置ではない。

## 構成
```
src/so101_demo/
  kinematics/     placo による FK / IK、上面からの手先目標、作業領域の拒否判定
  sim/            MuJoCo のシーン（SO-101 + 机 + キューブ 2 + 箱 2）、簡略把持、描画
  robot/          RobotBackend（関節指令・グリッパー・状態・停止）と MuJoCo 実装
  locate/         物体 ID -> 位置（sim の真値）
  plan/           契約 JSON のスキーマ検証（contracts/plan.schema.json, registry.json）
  execution/      pick / place プリミティブ、安全層、判定器、plan runner
  understanding/  Cosmos クライアント（一文）、Astra クライアント（strict function calling）、パイプライン
  ui/             承認画面と大画面（FastAPI + 静的 HTML/JS）
  evidence/       評価・動画生成スクリプト（uc_eval: 理解層の評価、stage1_g1: 固定試行と動画）
contracts/        契約 JSON スキーマ、登録物体、サンプル（ok / unsupported / needs_review / error）、評価用 manifest
serving/cosmos/   Cosmos Reason 2 2B を vLLM で起動する別 uv プロジェクト
docs/             brief（目的・要件）、レポート原稿、動画台本、コードの説明、評価記録（docs/evidence/）
tests/            pytest（GPU・vLLM・表示が要るものは `gpu` マークで既定スキップ）
```

## 動かし方
```bash
scripts/fetch-so101.sh                  # SO-101 モデル（TheRobotStudio/SO-ARM100 eecbe3e）を assets/so101/ に取得
uv sync
uv run pytest                           # 物理・IK・契約・runner・UI のテスト（GPU 不要）

# 理解層（GPU 機）: 別ターミナルで Cosmos を起動し、.env に Azure OpenAI の接続先を書く（.env.example）
serving/cosmos/serve.sh
uv run python -m so101_demo.evidence.uc_eval          # 評価動画 15 本を通し、docs/evidence/uc/ に記録

# 承認画面 + 大画面（GPU 機、描画あり）
MUJOCO_GL=egl uv run python -m so101_demo.ui --render

# 固定した 4 組合せの実行と動画（GPU 機）
MUJOCO_GL=egl uv run python -m so101_demo.evidence.stage1_g1 --approve-fixed-fixture --label 受け入れ --video
```

固定 revision: MuJoCo 3.13.0、SO-ARM100 `Simulation/SO101` eecbe3e、LeRobot `RobotKinematics` 5aa7455（`src/so101_demo/vendor/`）、Cosmos Reason 2 2B on vLLM 0.12.0。第三者コードのライセンスは `THIRD_PARTY_NOTICES.md`。

## 提出物
- 動画（1〜3 分、YouTube）: 台本は `docs/video-script.md`
- レポート: `docs/report.md`（PDF にして提出）
- コード: このリポジトリ

## 開発の進め方
Claude Code（メイン）と Codex（補助レビュー）が agmsg で連携し、GitHub Issue 単位でブランチを切って進める。プロセスの正本は `AGENTS.md`、目的とスコープは `docs/brief.md`、エージェント環境の準備は `docs/agent-workflow.md`。
