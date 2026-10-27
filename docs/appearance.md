# 表示の見栄え: 固定カメラ・無地背景・ラベル・1080p・フレームレート

- 作成: 2026-09-19（旧プロジェクト）。2026-09-20 にトレイを (0.19, ±0.085) から (0.22, ±0.09) に移して再測定。2026-10-27 に課題用に書き直した
- 実装: `src/so101_demo/sim/appearance.py`（視覚要素）、`src/so101_demo/evidence/record.py`（録画）、`src/so101_demo/evidence/overlay.py`（ラベル）。テスト: `tests/test_appearance.py`（物理に影響しないこと）、`tests/test_appearance_gpu.py`（`gpu`。初期状態の 1 フレームで、立方体の射影がセグメンテーション重心と一致すること、1080p のラベルが読める大きさで画面内に収まること）

## 結論
- 動画と表示画面に出す sim の絵は、固定カメラ 1 つ、無地の机と背景、色 + ラベル、1920×1080、移動区間 25 fps で作る。4 組合せ（red_cube / blue_cube × box_a / box_b）すべてで、両トレイ・両立方体・ラベル 4 枚が画面内に入り、録画パイプラインは 25 fps を上回った（下の「結果の要約」）
- 見栄えは物理に影響しない: `sim/appearance.py` は MjSpec の視覚要素（スカイボックス、ライト、offscreen サイズ、ヘッドライト）だけを足す。`tests/test_appearance.py` が物理配列・`opt`・2 s の軌道の一致を確認する
- 録画は `h264_nvenc`（GPU エンコード）を使い、無ければ libx264 に戻る。libx264 で CPU エンコードすると描画が遅くなり 25 fps を割ることがあった

## 仕様
| 項目 | 値 |
|---|---|
| トレイ | box_a (0.22, 0.09)、box_b (0.22, −0.09) m（2026-09-20）。正本は `contracts/registry.json` |
| カメラ | 固定 1 つ（自由カメラ）。lookat (0.19, 0.0, 0.08) m、distance 0.50 m、azimuth 180°（ベースの正面から）、elevation −22° |
| 机 | 平面、RGBA (0.93, 0.93, 0.90)、テクスチャなし |
| 背景 | 単色スカイボックス RGB (0.80, 0.84, 0.88)（`mjBUILTIN_FLAT`） |
| 照明 | ヘッドライト ambient 0.38 / diffuse 0.42 / specular 0.10 + スポットライト 1 灯 位置 (0.35, −0.20, 0.85)、注視 (0.19, 0, 0)、cutoff 70°、影あり（shadowsize 4096） |
| 出力 | 1920×1080、H.264（`h264_nvenc` preset p4 cq 28。無ければ libx264 crf 28 preset veryfast）、25 fps。動画は 実行中 区間のみ |
| ラベル | DejaVu Sans Bold 48 px、白文字、半透明黒地（α 175/255）、左端に実体色の帯 12 px。`Red cube` / `Blue cube` / `Tray A` / `Tray B`。物体ラベルは body 位置の射影の 120 px 上（搬送中も追従）、トレイは中心の射影の 28 px 下。重なりは物体を上・トレイを下に押して解消 |
| 射影 | 描画シーンの 2 つの `mjvGLCamera`（ステレオ対）の平均から計算。lookat が画像中心に写ること、セグメンテーション重心と 25 px 以内で一致することをテスト |
| フォント | 英字のみ（CJK フォントを前提にしない） |

## 設計時に分かったこと（2026-09-19、旧プロジェクト。数値は当時の GPU 機 RTX 4090 Laptop / WSL2）
| 項目 | 内容 | 対処 |
|---|---|---|
| WSL2 の EGL は既定で CPU 描画 | `MUJOCO_GL=egl` の既定では Mesa が llvmpipe を選び、1080p で 94 ms/フレーム | `/dev/dxg` がある WSL2 では `GALLIUM_DRIVER=d3d12` と `MESA_D3D12_DEFAULT_ADAPTER_NAME=NVIDIA` を MuJoCo の import 前に設定する（`evidence/gl_env.py`、`so101_demo.evidence` の import 時と `tests/conftest.py`）。NVIDIA 指定で 15〜21 ms |
| 30 fps は作れない | observer は制御 tick（`CONTROL_DT_S` = 0.02 s、50 Hz）ごとに呼ばれ、1/30 s は tick に量子化される | 出力 25 fps（tick 2 回に 1 枚）に固定 |
| 斜めのカメラは非対象を隠す | azimuth 145° / 仰角 −35° では、blue を掴む試行で red の可視画素が 318 px（しきい値 3000） | 24 候補をセグメンテーションで評価し、正面 azimuth 180° / 仰角 −22° / 距離 0.5 m に変更。全 fixture・全項目でしきい値の 1.7 倍以上 |
| 描画コストは解像度に依存しない | 1080p / 720p / 540p とも 16〜21 ms。WSL2 の D3D12 経路の固定コストが支配的 | 1080p 直接描画 |
| libx264 が描画を遅くする | libx264 のスレッドが動いている間は描画が 20 → 27〜28 ms に伸びる | `h264_nvenc` に変更: 描画 15 ms で安定、39 fps。NVENC が無い環境では libx264 に戻る（`record.h264_encoder`） |

## 計測方法
- **fps**: recorder が 1 フレーム描くごとに `time.perf_counter()` を記録。区間 = 各 fixture の 実行中（submit 受付〜完了。リセット中は observer を呼ばない）。実効 fps = (フレーム数 − 1) / (最後 − 最初)、最大間隔 = 隣接フレームの壁時計差の最大。合格 = 実効 fps ≥ 25 かつ最大間隔 < 1.0 s。Cosmos / vLLM は停止した状態で測った
- **可視性**: 1920×1080 のセグメンテーション描画を sim 時間 0.2 s ごと（5 Hz）に採取。毎サンプルで 対象 ≥ 3000 px、非対象 ≥ 3000 px、box_a / box_b の壁 4 枚合計 ≥ 2000 px、可動顎 ≥ 800 px。ラベル重畳前の描画で測る
- **文字高**: ラベル箱内の白画素（各チャネル ≥ 230）の行範囲。合格 ≥ 32 px。フォント 48 px で 37〜45 px
- **画面内**: トレイは可視画素の外接箱と画面端の最小距離、ラベルは地色の箱と画面端の最小距離。合格 ≥ 1 px。4 fixture を実行しての可視性・トレイ余裕の測定は旧プロジェクトで行い、そのテストは 2026-10-27 に削除した。今の `tests/test_appearance_gpu.py` は初期状態の 1 フレームについて、立方体の射影とセグメンテーション重心の一致（25 px 以内）と、ラベルの文字高（≥ 32 px）・画面端からの余白だけを確認する
- **物理への無影響**: `tests/test_appearance.py`（物理配列 39 種・`opt` 全項目・2 s 軌道の一致）

## 結果の要約（2026-09-20、旧プロジェクトでの測定。動画・キャプチャ・計測 JSON はこのリポジトリに持ち込んでいない）
- 4 組合せ 4/4 success、実効 fps 37.1〜39.1（NVENC）、最大間隔 243〜525 ms（基準 1.0 s 未満。平均間隔は 26 ms 前後）、可視性 全サンプル合（対象・非対象 最小 4225 px / しきい値 3000、トレイ 9951 px 以上 / 2000、顎 7390 px 以上 / 800）、文字高 37〜45 px
- 画面内: 両トレイとも端まで 267 px、ラベル 4 枚の最小余裕 247 px（`Tray A`）
- 動画 4 本 各 1.09〜1.19 MB
- 課題の提出動画に使う素材は、通しの Issue で撮り直す

## 作らないもの
フォトリアル化・専用レンダラー、自動追尾・カット割り、補助視点、生 JSON の表示制御、Cosmos 常駐下の fps 測定

## 参照
- `src/so101_demo/sim/appearance.py`、`src/so101_demo/evidence/record.py`、`src/so101_demo/evidence/overlay.py`、`src/so101_demo/evidence/gl_env.py`
- `tests/test_appearance.py`、`tests/test_appearance_gpu.py`
