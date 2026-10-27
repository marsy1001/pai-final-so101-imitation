# G1 固定 10 試行の manifest: 設計、開始位置の決め方、凍結の手順

- 作成: 2026-09-20（Issue #19）。**2026-09-21 に ADR 0008 の配置で作り直した**（トレイ (0.22, ±0.09)、両物体の開始位置を試行ごとに固定）。対象ゲート: G1（#13、2026-10-09）
- 正本: `contracts/fixtures/g1/manifest.json`。判定値: `src/so101_demo/execution/criteria.py`（0b / 4a から変更なし）。実行: `src/so101_demo/evidence/stage1_g1.py`。配置の余地の計測: `src/so101_demo/evidence/g1_layout.py`

## 結論
- 固定 10 試行の manifest は **ADR 0008 の 10 パターンそのもの**である。4 組合せ（red_cube / blue_cube × box_a / box_b）を各 2〜3 回、対象は毎回切替、**赤・青の両方**の開始 xy を試行ごとに固定する。どちらの物体も home に留まらない
- 開始位置の決め方が ADR 0008 で変わった: 旧「エージェントが sweep と規則で機械的に選ぶ（非対象は home）」→ 新「**ユーザーが描画で確認して 10 パターンを決め**、エージェントは規則（作業領域内・中心間距離 ≥ 60 mm・トレイ足元と重ならない・把持 TCP が作業領域内）と headless 実行で確認する」。開発試行の結果に合格数字を合わせない規則は変えていない
- **受け入れ**実行（母艦（GPU 機）、2026-09-21、`MUJOCO_GL=egl --label 受け入れ --video`、凍結 manifest `fe2d941`）: **10/10 成功**、開始配置 合（誤差 0.11 mm）、他物体の移動 0.00 mm、実行後リセット 合、失敗なし、1920×1080 / 25 fps / H.264 の動画 10 本（`docs/evidence/g1/trials.md`、`T{01..10}-*.mp4`）。先行する headless **参考**実行（同日、XPS 分）と全数値が一致する（決定的）。合格数字 10/10 はユーザーが #13 で**この実行の前に**固定しており、結果に合わせて決めたものではない。合否の宣言は #13 で行う
- 配置の根拠数値は `docs/evidence/g1/layout.md` に再現可能な形で入れた（幾何のみ、sim 実行なし）。**ADR 0008 の試算と完全に一致**する: 立方体が立てる格子点 / 2 物体の配置（無順序の組。赤青の割当てを区別すると 2 倍）= (0.19, ±0.085) 56 / 251、**(0.22, ±0.09) 68 / 807**、(0.23, ±0.09) 80 / 1405。旧配置では ADR の 10 パターンのうち 9 つが規則違反（T04 のみ可）で、配置変更なしにはこの manifest は作れない。なお (0.23, ±0.09) でも 10 パターンは成立する（この 10 試行だけでは配置を一意に決められない。トレイを奥へ寄せる根拠は ADR 0008 の余地の比較）
- 凍結: manifest を含む commit のハッシュを #13 に記録した時点で凍結。以後は書き換えない（手順は末尾）

## 試行表（`manifest.json` と同一。ADR 0008 の表、m、基部座標）
| 試行 | fixture（plan） | 対象 → 置き先 | 開始位置 red_cube | 開始位置 blue_cube |
|---|---|---|---|---|
| T01 | F1-red_cube-box_a | red → A | (0.16, −0.08) | (0.22, 0.02) |
| T02 | F2-blue_cube-box_b | blue → B | (0.16, 0.06) | (0.16, −0.04) |
| T03 | F3-red_cube-box_b | red → B | (0.20, 0.02) | (0.16, 0.08) |
| T04 | F4-blue_cube-box_a | blue → A | (0.16, −0.02) | (0.23, 0.03) |
| T05 | F1-red_cube-box_a | red → A | (0.16, 0.04) | (0.22, −0.03) |
| T06 | F2-blue_cube-box_b | blue → B | (0.22, 0.03) | (0.16, −0.08) |
| T07 | F3-red_cube-box_b | red → B | (0.16, −0.01) | (0.16, 0.08) |
| T08 | F4-blue_cube-box_a | blue → A | (0.16, −0.06) | (0.20, 0.00) |
| T09 | F1-red_cube-box_a | red → A | (0.18, −0.03) | (0.16, 0.06) |
| T10 | F2-blue_cube-box_b | blue → B | (0.16, 0.08) | (0.21, −0.02) |

組合せの回数: red→A 3（T01, T05, T09）、blue→B 3（T02, T06, T10）、red→B 2（T03, T07）、blue→A 2（T04, T08）。中心間距離は 72〜125 mm（規則 60 mm 以上）。plan は 4a の固定 JSON（`contracts/fixtures/4a/`）をそのまま使い、承認は `--approve-fixed-fixture` だけが与える（`docs/execution-runner.md`）。試行の手順は 毎回: リセット（判定付き）→ `PlanRunner.stage` で両物体を開始位置へ置いて 1.0 s 静定・配置判定 → `PlanRunner.submit` → 判定 → リセット（判定付き）。開始配置が不合格の試行は submit しない（`start_check_failed`、指令 0 で記録）。成功 = 判定器 success（搬送監査含む）かつ 開始配置 合 かつ 実行後リセット 合。

## 規則と確認
`tests/test_g1_manifest.py`（`plan/manifest.py` の `check_manifest` が単一の実装。`stage1_g1` は規則を破る manifest を実行しない）:

| 規則 | 内容 |
|---|---|
| (a) | 各 plan がスキーマ検証を通り、登録 ID・対象・置き先が試行と一致する |
| (b) | 4 組合せが各 2 回以上 |
| (c) | 連続する試行で対象が切り替わる |
| (d) | **両物体**の開始位置が 作業領域内・中心間距離 ≥ 60 mm・トレイ足元（内寸 60 + 壁 5 mm）と重ならない・把持 TCP（中心から基部側へ 23.5 mm）が作業領域内 |
| (e) | 試行 ID が一意、10 試行 |

2026-09-21 の変更（ADR 0008）: 規則 (d) から「非対象は home」を外し、代わりに幾何規則を**両物体**に適用する。ラベル（`start_label`）と名前付き位置（`positions_m`、P1_R など）は新設計で意味を失うため manifest と記録から削除した。緩和ではない（対象だけでなく非対象にも規則がかかる）。試行表が ADR 0008 の表と一致することもテストが確認する。

## 開始位置の決め方（ADR 0008、2026-09-20 のユーザー決定）
1. ユーザーが描画（`tmp/g1-layout-preview/`、2026-09-20）で配置を確認し、10 パターンを決める
2. エージェントは規則（上表 (d)）を `check_manifest` で確認し、headless 参考実行で 10 試行を通す
3. 通らない試行があればその事実と数値を記録し、パターンの差し替えはユーザーに決めてもらう（エージェントは成功する位置を探し直さない）
4. 旧方式（候補 sweep による P1 / P2 の機械的選定、非対象は home）は廃止した。旧配置での sweep 結果は `docs/evidence/g1/tray19/candidates.md` に残す。生成モジュール `so101_demo.evidence.g1_candidates` は削除した（commit eac8f89 に残る）

### T07 の (0.16, 0.00) について
ADR 0008 は「P07 は当初 (0.16, 0.00) が headless で `not_seated` だったので (0.16, −0.01) にずらした」と記録している。2026-09-21 に同じ開始位置を今のコードで確認したところ、**(0.16, 0.00) でも success**（解放後 中心 (0.2185, −0.0889)、傾き 0.00°）になった。間に入った変更は Issue #21（置き姿勢をトレイに合わせ、解放後のずれを縮めた。merge de7724c）である。manifest は ADR どおり (0.16, −0.01) のままにした（開始位置はユーザーの決定であり、エージェントが結果を見て戻さない）。

## 受け入れ実行（2026-09-21、母艦（GPU 機）、`--label 受け入れ --video`）
`MUJOCO_GL=egl uv run python -m so101_demo.evidence.stage1_g1 --approve-fixed-fixture --label 受け入れ --video` → `docs/evidence/g1/trials.md` + `trials.json` + `T{01..10}-*.mp4`。manifest は凍結ハッシュ `fe2d941` のまま（差分なし）。

同じ 2026-09-21 の headless **参考**実行（XPS 分、`--label 参考`）は同じファイルに書くので上書きされた。参考実行の記録は commit `fe2d941` に残る（`git show fe2d941:docs/evidence/g1/trials.md`）。下の値は両実行で**完全に一致**する（決定的。違うのは記録日時・壁時計・動画欄だけ）。

| 項目 | 値 |
|---|---|
| 結果 | 10/10 success、失敗なし |
| 開始配置の判定 | 全試行 合（物体位置差 0.11 mm、傾き 0.00°） |
| 他物体の移動 | 全試行 0.00 mm（最大 1.5e-6 mm） |
| 解放後の中心 | 置き先中心から x −1.00〜−1.53 mm、y −2.32〜+0.92 mm、平面距離 1.02〜2.72 mm（最大 T08）。傾き 0.00°。内寸の余裕は片側 10 mm |
| 搬送監査 | TCP 距離最大 29.1〜29.6 mm（上限 45 mm）、ゾーン逸脱 0.0 mm、解放後監査 191 tick |
| 実行後リセット | 全試行 合 |
| 指令数 / sim 時間 | 316 / 試行、9.8 s / 試行 |
| 動画（受け入れのみ） | 10 本、1920×1080・25 fps・H.264（encoder `h264_nvenc`、GL `D3D12 (NVIDIA GeForce RTX 4090 Laptop GPU)`）。1 本 1.08〜1.31 MB（上限 10 MB） |

旧配置（2026-09-20、`tray19/trials.md`）と比べた変化: 解放後の中心の **x 方向のずれ**が **+3.49〜+5.55 mm から −1.00〜−1.53 mm** に、**平面距離**が **最大 5.62 mm から最大 2.72 mm** に縮んだ（y 方向は ±2.4 mm 程度で変わらない）。原因は #21 の置き姿勢整合で、トレイ移動によるものではない。

## #9 所見との関係（閉指令時の立方体は TCP から約 29.6 mm、置き先で把持方向が径方向に回る）
- #9 は「回転が大きく offset が壁側へ向く組合せは `not_seated` になり得る」とした。旧配置の sweep 50 run では `not_seated` が 6 run あり、隣接格子点で成否が変わる例（blue (0.22, 0.02) 成功 / (0.22, 0.01) 失敗）もあった（`tray19/candidates.md`）
- 新配置 + #21 の置き姿勢整合の後、この 10 試行はすべて成功し、解放後の中心のずれは **x −1.00〜−1.53 mm、平面距離 最大 2.72 mm**（T08。内寸の余裕は片側 10 mm）に収まっている
- 新配置での掃引は、**実施済みの範囲**と**未評価の範囲**を分けて #13 に引き継ぐ:
  - 実施済み（#21、新配置・置き姿勢整合後、`docs/evidence/0b/place-margin.md`）: G1 の規則を満たす 10 mm 格子の開始点 × 2 物体 × 2 置き先で**非対象を home に置いた** 94 run が全 success（着地余裕 最小 5.6 mm）、y = 0 列（x 16〜23 cm、対象のみ配置）32 run も全 success
  - 未評価: **両物体を任意の格子点に置く組合せ**（本 manifest の 10 パターン以外）。旧配置で見えた「隣接格子点で成否が変わる」性質がこの範囲で消えた証拠はない。リスクが消えたとは宣言しない
- 60 mm 規則の限界（可動顎は径方向 ≈ +x に開くので、非対象が対象の +x 側 60 mm にあると当たることがある。旧配置の sweep で 16〜51 mm 動いた例あり）は規則として残っている。この 10 パターンでは最小の中心間距離が 72 mm で、実測の他物体の移動は 0.00 mm

## 凍結の手順
1. XPS 分のマージ後、`main` 上の `contracts/fixtures/g1/manifest.json` を含む commit のハッシュを **#13 にコメント**する（「manifest 凍結: `<hash>`、`git show <hash>:contracts/fixtures/g1/manifest.json`」）。ユーザーの合格数字も同じ Issue に記録される（2026-10-06 頃）
2. 母艦分（`task/19-g1-manifest-gpu`）は、#13 に記録されたハッシュの manifest（差分なし）で `MUJOCO_GL=egl uv run python -m so101_demo.evidence.stage1_g1 --approve-fixed-fixture --label 受け入れ --video` を実行し、`trials.md` の「commit」欄がそのハッシュ（完全形）を示すことを確認する（`stage1_g1` は、manifest を最後に変えた commit が `main` に無いか、作業ツリーで変更されていると「未凍結」と書く）
3. 凍結後の変更は新しい manifest として扱い、理由と旧 manifest のハッシュを #13 に記録する。旧 manifest の結果は残す（demo-plan 共通規約「評価前固定」「測定条件の保持」）

## 出典
- `docs/demo-plan.md` 判断期限表 G1 行、共通規約（評価前固定、合格基準はユーザーが固定）
- `docs/adr/0008-move-trays-to-far-corners-and-fix-both-start-positions.md`（Accepted 2026-09-20: トレイ (0.22, ±0.09)、10 パターンの表、証拠のやり直し）、ADR 0006（端末分担）、ADR 0007（簡略把持）
- Issue #19（本文、Started コメント 2026-09-20 / 2026-09-21、追補コメント 2026-09-20、範囲変更コメント 2026-09-20）、#13（ゲート G1）、#20（配置変更）、#21（置き姿勢の整合）、#9 コメント「XPS 分: 補足の修正と実装中の所見」（2026-09-19）
- `contracts/registry.json`（home、トレイ位置・寸法）、`src/so101_demo/kinematics/top_down.py`（作業領域）、`src/so101_demo/execution/primitives.py`（TCP offset、可動顎の向き）、`src/so101_demo/execution/criteria.py`（判定値、リセット許容値）
- `docs/evidence/g1/layout.md` / `layout.json`（配置比較）、`docs/evidence/g1/trials.md` / `trials.json`（参考実行 2026-09-21）、`docs/evidence/g1/tray19/`（旧配置の候補 sweep と参考実行、2026-09-20）
- `docs/evidence/0b/place-margin.md` / `.json`（#21、新配置・置き姿勢整合後の掃引 94/94 と y = 0 列 32/32、着地余裕。修正前後の比較）
