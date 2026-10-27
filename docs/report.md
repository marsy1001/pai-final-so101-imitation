# 人の作業動画から SO-101 が同じ片付けを再現する: 理解層と承認を挟んだ動画模倣

関連: [[brief]]

## 結論
人が机の上でキューブを箱へ片付ける約 10 秒の動画を、Cosmos Reason 2 が一文にし、GPT-6 Astra が契約 JSON に構造化し、人が画面で承認すると MuJoCo 上の SO-101 が同じ pick / place を再現する作品を作った。4 組合せすべてで「動画 → 承認 → 実行成功」を 1 コマンドの通しで記録し、対応外の動画（ペン）は動かさずに止められた。理解層の精度は対応内 12 本中 8 本の完全一致にとどまり、対応外 3 本のうち 1 本を誤って実行可能と返した。

## 概要
録画台の上で人が赤 / 青のキューブを左右どちらかの箱（A / B）へ移す動画が入力である。流れは次の 5 段で、各段の出力が次の段の唯一の入力になる。

1. 人の動画（2×2 の 4 組合せ）
2. Cosmos Reason 2 2B（ローカル vLLM）が「何をどこへ動かしたか」を英語の一文にする
3. GPT-6 Astra がその一文だけを読み、契約 JSON（`status` / `target` / `destination` / `steps` / `note`）にする。登録外の物体や動作は `unsupported`、判断できないものは `needs_review`
4. 人がブラウザの承認画面で日本語の要約と対象・置き先を確認し、「これで動かす」を押す
5. MuJoCo 上の SO-101 が、承認済み JSON を IK で関節角に変換した pick / place で実行する

真似るのは「何をどこへ」という手順と結果であり、手の軌道や物体の配置ではない。

## 目的
課題テーマ「AI×ロボティクスによる創造的タスク」に対し、**動画からの模倣を、理解層と人の承認を挟んで安全に行える**ことを示す。人に教示装置を持たせず、普段どおりの作業を撮るだけでロボットへの指示にできれば、指示の手間は大きく減る。一方で動画の読み取りは誤るので、誤りを「動かさない」判断と人の確認で止められる構造を作品の中心に置いた。問いは 3 つである（`docs/brief.md`「問い・スコープ」）。

- 通し（動画 → 一文 → JSON → 承認 → sim 実行 → 録画）を 1 コマンドで再現し、4 組合せの実行動画が撮れるか
- 理解層の精度と失敗の型を、正直に示せるか
- 1〜3 分の動画で、流れが説明なしで伝わるか

## 仕組み
| 構成要素 | 内容と固定 revision |
|---|---|
| 動画理解 | Cosmos Reason 2 2B（`nvidia/Cosmos-Reason2-2B`、revision `9ce19a19…`）を vLLM 0.12.0 でローカル起動。4 fps で 40 フレームを入力し、一文を得る。`max_tokens` 1024、タイムアウト 30 秒。`finish_reason` が `stop` でなければ失敗として Astra を呼ばない |
| 構造化 | GPT-6 Astra（Azure OpenAI Responses API、API 報告モデル `gpt-6-astra`）に一文と登録 ID だけを渡す。動画やフレームは送らない。strict function calling で `submit_plan` を 1 回だけ呼ばせ、引数を契約 JSON schema（`contracts/plan.schema.json`）で再検証する |
| 契約 JSON | `status` は `ok` / `unsupported` / `needs_review` / `error`。`ok` のときだけ `steps`（pick → place）を持ち、`target` と `destination` は登録 ID（`contracts/registry.json`: red_cube、blue_cube、box_a、box_b）に限る。それ以外の組合せは schema が拒む |
| 承認画面 | FastAPI + HTML / JS。待機 → 読み取り中 → 承認待ち → 実行中の状態機械。実行に至る経路は 承認・訂正・手動 の 3 つで、「対応外にする」（拒否）を含めた 4 操作が人の判断の全部。訂正と手動は `rescued` として記録し、自動理解の成功には数えない |
| RunGate | 読み取りごとに run ID を発行し、取り消し・待機上限（62 秒）を過ぎた結果は画面に出さず実行も起こさない。遅れて届いた古い結果が誤って動くのを防ぐ |
| 実行器 | `PlanRunner.submit` が sim への唯一の入口で、承認マーク付きのプランしか受けない。承認時と最初の指令の直前の 2 か所で契約 JSON へ往復検証し、違反は指令 0 で `rejected` |
| sim と機体 | MuJoCo 3.13.0、SO-101 モデルは TheRobotStudio/SO-ARM100 `Simulation/SO101`（eecbe3e）。机、キューブ 2 個、箱 2 個を固定位置に置く。把持は「閉じたとき顎の間に登録物体が 1 個だけあれば保持」の簡略把持 |
| IK と安全層 | LeRobot `RobotKinematics`（placo、5aa7455）を `so101_new_calib.urdf` で使い、上面からの把持目標を解く。直線区間は 8 分割で IK、50 Hz で線形補間。作業領域・関節限界・関節速度上限（90 deg/s）の違反はクリップせず拒否 |
| 判定 | pick / place の成否は物体 pose とグリッパー値だけから判定し、保持リストは使わない。実行後のリセットが通らなければ成功に数えない |

## 工夫点
- **「動かさない」判断を理解層の出力に組み込んだ。** 契約 JSON の `status` に `unsupported` / `needs_review` を持たせ、登録外の物体（ペン）や動作（積む）、判断できない動画では実行可能なプランを返さない。schema で `ok` 以外に `steps` を持てなくしたので、`unsupported` / `needs_review` と**判定された**プランは実行に流れ込めない。ただし理解層が誤って正規の `ok` を返した場合（uc-15）は形式上正しく、schema も往復検証も通るので、止めるのは次の承認画面で人が見る工程になる。
- **人の承認を必ず通す関所。** 実行器は承認マーク付きのプランしか受けず、承認マークを作る関数は 2 つ（画面の承認、開発用 fixture）しかない。開発用は記録に `dev-fixed-fixture` が残り、人が承認した記録と混ざらない。RunGate が古い結果を捨てるので、画面を操作している最中に遅れた読み取り結果が動き出すこともない。
- **1 コマンドで通しが再現できる。** `python -m so101_demo.ui --videos ... --record` で、動画の選択 → 読み取り → 承認 → sim 実行 → 録画と記録（1 run 1 行の `runs.jsonl`）まで動く。記録は検査コマンドで機械的に確かめ、文書は記録だけから再生成する。
- **大画面は人の動画と sim を切り替える。** 動画を選ぶと表示画面に人の作業動画が繰り返し流れ、承認した瞬間に sim の描画へ切り替わり、終わると人の動画に戻る。画面収録だけで「人の動画 → 読み取り → 承認 → ロボットが同じ作業」の順が伝わる。
- **Astra には動画を送らない。** 一文と登録 ID だけを渡すので、外部 API に映像が出ない。失敗の原因も「文が間違った」か「文の解釈が間違った」かに切り分けられる。

## 結果
### 理解層の精度（受け入れ動画 15 本、2026-09-29、`docs/evidence/uc/eval-r2.md`）
| 項目 | 値 |
|---|---|
| 対応内 12 本の完全一致（status・target・destination・steps が gold と一致） | 8/12 |
| 組合せ別: red→A / red→B / blue→A / blue→B | 1/3 / 2/3 / 2/3 / 3/3 |
| 対応外 3 本で実行可能プランを返した数（誤受理） | 1/3（uc-15） |
| 所要（動画投入 〜 検証済み最終 JSON、15 本） | 中央値 19.7 秒、最大 33.2 秒 |
| 診断値: Cosmos の一文と事実の一致 / Astra の文への忠実性 | 10/14 / 14/14 |

対応内の失敗 4 本の内訳は、対応内への拒否（`needs_review`）2、誤ったプラン 1、その他のエラー 1（Cosmos の `finish_reason` が `length`）。対応外 3 本のうちペン（uc-13）と積み重ね（uc-14）は `unsupported` で正しく止まり、手に隠れて画面外へ運ばれた動画（uc-15）だけが `ok / blue_cube / box_a` と誤受理された。

### 通し実行（2026-10-27、`docs/evidence/e2e/runs.md`）
| 項目 | 値 |
|---|---|
| 対応内 4 組合せで 承認 → runner `success` | 4/4（uc-02、uc-05、uc-08、uc-10） |
| 対応外 uc-13（ペン） | 理解層 `unsupported`、「対応外にする」で停止、送った指令 0 |
| 録画 | 4 本、1920×1080、各 1.09〜1.19 MB、実効 fps 23.3〜24.1 |
| 所要（理解 / 実行） | 理解 8.23〜10.7 秒、実行 10.22〜10.52 秒 |

### 提出動画の構成
編集なしで、承認画面と表示画面の画面収録を次の 5 本の順に流す。各本の操作は同じで、動画を選ぶ（表示画面に人の動画）→「読み取り開始」→ 承認待ちで要約と対象・置き先を見る → 操作 → 完了で待機に戻る。

1. uc-02: 赤 → 箱 A。「これで動かす」で sim が赤を A へ
2. uc-05: 赤 → 箱 B。同じく承認して実行
3. uc-08: 青 → 箱 A。同じく承認して実行
4. uc-10: 青 → 箱 B。同じく承認して実行
5. uc-13: ペン → 箱 B。理解層が `unsupported` を返し、「対応外にする」で動かさずに終わる

## 考察
**失敗の型と原因の仮説。** 15 本の診断値は、Astra の文への忠実性が 14/14 だったのに対し、Cosmos の一文と事実の一致が 10/14 であった。つまり失敗 5 本はすべて一文の段で起きている。

- 対応内への拒否 2（uc-01、uc-03）: 一文が「赤を左へ、青を右へ」と、映像にない 2 つ目の操作を付け足した。Astra は 1 件に絞れないとして `needs_review` を返し、文には忠実だった。動画の最初と最後に両方のキューブが見えるため、静止している青にも動作を割り当てたと考えている
- 誤ったプラン 1（uc-04）: 一文が置き先の左右を取り違え、そのままプランになった。広角で撮った動画で、左右の手がかりが弱い
- Cosmos の暴走 1（uc-07）: 推論トレースが `max_tokens` 1024 に達し、24.80 秒で `finish_reason` が `length` のまま終了した。`finish_reason` 検査で失敗として扱い、Astra は呼ばれていない。止めたのはトークン上限で、30 秒のタイムアウトは別の打ち切り条件であり、この試行では発火していない
- 対応外の誤受理 1（uc-15）: 青が手に隠れて画面外へ運ばれ、両トレーは空のままなのに、一文は「左のトレーへ」と見えない置き先を埋めた。指示文には「分からなければ分からないと言う」とあるが効かなかった。この型は承認画面で人が止めるしかない

**やらなかったこと。** 指示文は受け入れ 15 本の前の版の結果を見て 1 度直しており、上の点数はその後の値で、初見の動画では下がりうる。それ以上の再調整は行わなかった（`docs/brief.md`「やらないこと」）。把持は簡略把持のままで、接触の物理で掴む改善もしていない。手の軌道や物体配置の複製、実機、VLA による模倣学習も範囲外とした。

**反省。** 精度の基準（対応内 10/12 以上、誤受理 0）には届かず、理解層は不合格のまま提出する。それでも通しが成立したのは、誤りを止める構造（`status`、schema、承認、RunGate、往復検証）を先に作ったからである。ただし構造が機械的に止めるのは、`unsupported` / `needs_review` と判定されたプラン、形式や登録 ID に違反したプラン、取り消し・期限切れの古い結果までで、uc-15 のように誤った正規の `ok` は人が承認画面で気づいて「対応外にする」を押すことを前提にしている。人が見落として承認すれば実行される。通しで指令 0 を実証したのは `unsupported` と判定された uc-13 であり、uc-15 を人が拒否する通し試行は行っていない。次に効くのは、一文を 1 本ではなく複数回生成して一致を見ることと、uc-15 のように置き先が見えない動画を指示文の例で示すことだと考えている。

## 参照元
- 課題の要件と作品の範囲: `docs/brief.md`
- 理解層の精度と失敗の型: `docs/evidence/uc/eval-r2.md`、固定構成 `docs/evidence/uc/config-r2.json`
- 通し実行の記録: `docs/evidence/e2e/runs.md`、`docs/evidence/e2e/runs.jsonl`、録画 `docs/evidence/e2e/*.mp4`
- 運用値（`max_tokens`、タイムアウト）の根拠: `docs/evidence/s1/diagnosis.md`、`docs/evidence/s1/serving.md`
- 承認画面・実行器・機体境界の設計: `docs/approval-screen.md`、`docs/execution-runner.md`、`docs/robot-backend.md`
- 契約 JSON: `contracts/plan.schema.json`、`contracts/registry.json`。評価動画: `contracts/fixtures/uc/videos.json`
- Cosmos Reason 2 2B: https://huggingface.co/nvidia/Cosmos-Reason2-2B
- vLLM: https://github.com/vllm-project/vllm
- Azure OpenAI Responses API: https://learn.microsoft.com/azure/ai-foundry/openai/how-to/responses
- MuJoCo: https://github.com/google-deepmind/mujoco
- SO-101 モデル: https://github.com/TheRobotStudio/SO-ARM100（`Simulation/SO101`、eecbe3e）
- LeRobot `RobotKinematics`: https://github.com/huggingface/lerobot（5aa7455、`THIRD_PARTY_NOTICES.md`）
