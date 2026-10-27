# 見栄え 最小版: 固定カメラ・無地背景・ラベル・1080p・フレームレート（Issue #10、2026-09-19）

> 2026-09-20 ADR 0008 でトレイを (0.19, ±0.085) から (0.22, ±0.09) に移し、Issue #20 の母艦分で新配置を再測定した。この文書の数値・共通条件は新配置（正本 `docs/evidence/appearance/report.md`、`report.json`）。旧配置の測定は `docs/evidence/appearance/tray19/` に残してある。置きの yaw と掴み位置の補正（Issue #21、2026-09-20）を入れたあとの測定である。

## 結論
- demo-plan「見栄えの最低ライン」のうち、第 1 段の部品だけで閉じる 5 項目（固定カメラ、無地の机と背景、色 + ラベル、1080p 出力、移動区間 25 fps 以上）を `sim-v1-simple` の 4 組合せで、新配置（トレイ (0.22, ±0.09)）でも満たした。証拠は `docs/evidence/appearance/report.md` と同じディレクトリの動画 4 本・キャプチャ 16 枚。旧配置の測定は `docs/evidence/appearance/tray19/`
- **状態表示（録画中 / 読み取り中 / 承認待ち / 実行中 / シミュレーション / 手動 / 事前録画）はこの Issue に含めない**（ユーザー決定 2026-09-19）。録画・読み取り・承認は第 2 段の部品で、UI の実装方式は 3b の前に ADR で決める約束のため。3b の UI ADR 後に Issue「見栄え 状態表示」で満たす。demo-plan は書き換えない（ADR 0004）
- フレームレートは **Cosmos / vLLM 停止時の仮合格**。本判定は第 3 段 同時常駐。母艦（RTX 4090 Laptop、WSL2）で 1080p 描画は 1 フレーム 17〜21 ms、録画パイプライン全体で **37〜39 fps（NVENC、新配置 2026-09-20 の正本実行。同日の再実行では 37〜41 fps の幅）**。libx264 で CPU エンコードすると描画が 20 → 28 ms に遅くなり 24〜27 fps で基準を割ることがあったため、venue 用 recorder は NVENC を使う（無ければ libx264）。Cosmos 常駐時の余裕は第 3 段で測る
- 見栄えは物理に影響しない: `sim/appearance.py` は MjSpec の視覚要素（スカイボックス、ライト、offscreen サイズ、ヘッドライト）だけを足す。`tests/test_appearance.py` が物理配列・`opt`・2 s の軌道の一致を確認し、4a の受け入れ実行を再実行しても指令数・判定が `main` と一致した

## 発見（この Issue で分かったこと）
| 項目 | 内容 | 対処 |
|---|---|---|
| 母艦の EGL は CPU 描画だった | `MUJOCO_GL=egl` の既定では Mesa が llvmpipe を選ぶ。styled 1080p で 94 ms/フレーム、640×480 でも 37 ms（4a の動画もこれで作られていた） | WSL2（`/dev/dxg` あり）では `GALLIUM_DRIVER=d3d12` と `MESA_D3D12_DEFAULT_ADAPTER_NAME=NVIDIA` を `so101_demo.evidence` の import 時と `tests/conftest.py` で設定（`evidence/gl_env.py`）。MuJoCo import 前に必要。既定アダプタは Intel 内蔵 GPU（23 ms）で、NVIDIA 指定で 15〜21 ms |
| 30 fps は作れない | observer は制御 tick（`CONTROL_DT_S` = 0.02 s、50 Hz）ごとに呼ばれ、1/30 s は tick に量子化されて実質 25 fps になる | 出力 25 fps（tick 2 回に 1 枚）に固定。Started の「30 fps」は Issue コメントで修正 |
| 旧カメラは非対象を隠す | azimuth 145° / 仰角 −35° では blue を掴む F2 / F4 で red の可視画素が 318 px（しきい値 3000） | 24 候補をセグメンテーションで評価し、正面 azimuth 180° / 仰角 −22° / 距離 0.5 m / lookat (0.19, 0, 0.08) に変更。全 fixture・全項目でしきい値の 1.7 倍以上 |
| 描画コストは解像度に依存しない | 1080p / 720p / 540p とも 16〜21 ms（持続 300 フレーム）。WSL2 の D3D12 経路の固定コストが支配的 | 720p 拡大は採らない（利点がない）。1080p 直接描画 |
| libx264 が描画を遅くする | 最初の fixture 32〜35 fps、以降 24〜27 fps。フレーム内訳を測ると、libx264 のスレッドが動いている間は描画（D3D12 経路、CPU 側の処理を含む）が 20 → 27〜28 ms に伸びる。Renderer の共有・x264 preset・解像度では変わらない | `h264_nvenc`（GPU エンコード）に変更: 描画 15 ms で安定、全 fixture 39 fps。ffmpeg に NVENC が無い環境では libx264 に戻る（`record.h264_encoder`）。同じ GPU で Cosmos が動く条件は第 3 段で測る |

## 仕様（`src/so101_demo/sim/appearance.py`、`src/so101_demo/evidence/record.py`、`src/so101_demo/evidence/overlay.py`）
| 項目 | 値 |
|---|---|
| トレイ | box_a (0.22, 0.09)、box_b (0.22, −0.09) m（ADR 0008、2026-09-20）。正本は `contracts/registry.json` |
| カメラ | 固定 1 つ（自由カメラ）。lookat (0.19, 0.0, 0.08) m、distance 0.50 m、azimuth 180°（ベースの正面から）、elevation −22°（新配置でも変更なし: 両トレイとラベルが余裕をもって画面内に入る。下表「画面内」） |
| 机 | 平面、RGBA (0.93, 0.93, 0.90)、テクスチャなし（0b から変更なし） |
| 背景 | 単色スカイボックス RGB (0.80, 0.84, 0.88)（`mjBUILTIN_FLAT`） |
| 照明 | ヘッドライト ambient 0.38 / diffuse 0.42 / specular 0.10 + スポットライト 1 灯 位置 (0.35, −0.20, 0.85)、注視 (0.19, 0, 0)、cutoff 70°、影あり（shadowsize 4096） |
| 出力 | 1920×1080、H.264（`h264_nvenc` preset p4 cq 28。無ければ libx264 crf 28 preset veryfast）、25 fps、`<fixture>-<config>-1080p.mp4`。動画は 実行中 区間のみ |
| ラベル | DejaVu Sans Bold 48 px、白文字、半透明黒地（α 175/255）、左端に実体色の帯 12 px。`Red cube` / `Blue cube` / `Tray A` / `Tray B`。物体ラベルは body 位置の射影の 120 px 上（搬送中も追従）、トレイは中心の射影の 28 px 下。重なりは物体を上・トレイを下に押して解消 |
| 射影 | 描画シーンの 2 つの `mjvGLCamera`（ステレオ対）の平均から計算。lookat が画像中心に写ること、セグメンテーション重心と 25 px 以内で一致することをテスト |
| フォント | 英字。母艦に CJK フォントが無く sudo も使えない（2026-09-19）。状態表示（日本語）を作る Issue でフォント導入を決める |

## 計測方法（Issue #10 Started コメント 2026-09-19 で固定。30 fps → 25 fps のみ修正）
- **fps**: recorder が 1 フレーム描くごとに `time.perf_counter()` を記録。区間 = 各 fixture の 実行中（submit 受付〜完了。リセット中は observer を呼ばない）。実効 fps = (フレーム数 − 1) / (最後 − 最初)、最大間隔 = 隣接フレームの壁時計差の最大。合格 = 実効 fps ≥ 25 かつ最大間隔 < 1.0 s。Cosmos / vLLM 停止（`nvidia-smi` 使用 446 MiB）
- **可視性**: 1920×1080 のセグメンテーション描画を sim 時間 0.2 s ごと（5 Hz）に採取（動画とは別パス。物理は決定的で outcome と指令数の一致を確認）。毎サンプルで 対象 ≥ 3000 px、非対象 ≥ 3000 px、box_a / box_b の壁 4 枚合計 ≥ 2000 px、可動顎 ≥ 800 px。ラベル重畳前の描画で測る（ラベルが物体にかかる分は含まない。物体ラベルは立方体の上に置き、重ならないようにしてある）
- **文字高**: キャプチャ（encoder に送った生フレーム PNG）上で、ラベル箱内の白画素（各チャネル ≥ 230）の行範囲。合格 ≥ 32 px。フォント 48 px で 37〜45 px
- **画面内**（新配置で Issue #20 が追加）: トレイは可視性と同じセグメンテーション採取から、可視画素の外接箱と画面端の最小距離を測る（見える画素が 0 のサンプルは −1 = 確認できず）。ラベルは録画した全フレームと 4 枚のキャプチャで、地色の箱（文字 + 余白 + 色帯）と画面端の最小距離を測る（搬送中はラベルが物体を追うため、キャプチャだけでは足りない）。合格 ≥ 1 px（0 = 端に接する、負 = 切れている）。キャプチャが 4 枚そろわない fixture は 否。`tests/test_appearance_gpu.py` が 4 fixture のトレイ余裕とラベル余裕を同じしきい値で確認する
- **物理への無影響**: `tests/test_appearance.py`（物理配列 39 種・`opt` 全項目・2 s 軌道の一致）と、4a 受け入れ実行の再実行結果を `main` の `runs.json` と照合（outcome・指令数・判定）

## 結果の要約（新配置、2026-09-20。正本: `docs/evidence/appearance/report.md`、`report.json`）
- 4 組合せ 4/4 success、実効 fps 37.1〜39.1（NVENC）、最大間隔 243〜525 ms（基準 1.0 s 未満。平均間隔は 26 ms 前後）、可視性 全サンプル合（対象・非対象 最小 4225 px / しきい値 3000、トレイ 9951 px 以上 / 2000、顎 7390 px 以上 / 800）、文字高 37〜45 px
- 画面内: 両トレイとも端まで 267 px、ラベル 4 枚の最小余裕 247 px（`Tray A`）。録画した全フレーム（各 245 枚）とキャプチャ 4 枚の両方で測って全 fixture 合。キャプチャ（例 `captures/F3-red_cube-box_b-carry.png`）でも両トレイとラベル 4 枚が入っている
- 動画 4 本 各 1.09〜1.19 MB。キャプチャ 16 枚（start / lift / carry / release × 4）
- 4a の 640×480 動画も同じカメラで新配置に再生成（`docs/evidence/4a/`、受け入れ実行 4/4、各 0.11 MB）
- 旧配置（トレイ (0.19, ±0.085)、`tray19/`）は 実効 fps 37.9〜38.9、最大間隔 284〜318 ms、可視性 最小 5276 px、文字高 37〜45 px、動画 各 0.35〜0.40 MB。新配置で fps は同等、可視性の最小は F4（blue_cube → Tray A）の非対象 red_cube が 4225 px まで下がるが、しきい値 3000 は上回る

## 作らないもの（demo-plan の線引き）
フォトリアル化・専用レンダラー、自動追尾・カット割り、補助視点、生 JSON の表示制御、Cosmos 常駐下の fps（第 3 段）、状態表示（上記）。G1 の受け入れ 10 試行はこの動画では行わない

## 参照
- `docs/demo-plan.md`「見栄えの最低ライン」「機器配置」、第 1 段 見栄え 行
- Issue #10（Started コメントの固定値、中断コメントの探索記録）、Issue #9（4a）、ADR 0006（端末分担）、ADR 0007（簡略把持）
