# 置き余裕の改善: 置きの yaw をトレイに合わせ、把持オフセットを置きで補正する

- 作成: 2026-09-20（旧プロジェクト）。2026-10-27 に課題用に書き直した。対象: 簡略把持（`sim-v1-simple`）での pick → place
- 実装: `src/so101_demo/kinematics/top_down.py`（向きの規約）、`src/so101_demo/execution/primitives.py`（pick / place）。計測: `src/so101_demo/evidence/place_margin.py`（`--out` に `place-margin.md` + `.json` を書く）。テスト: `tests/test_execution_pick_place.py`（固定試行ごとの着地余裕）
- 変えなかったもの: `contracts/registry.json`、判定値（`criteria.py`）、判定器（`judge.py`）、接触設定（MJCF・`sim/scene.py`）、簡略把持の規則（`sim/grasp.py`）、作業領域（`WORKSPACE_MIN/MAX`。境界判定に 1 µm の丸め許容を足したが領域は広げていない、下記）

## 結論
トレイを (0.22, ±0.09) に移した配置で固定試行 T2 が壁に乗って失敗した原因は 2 つあり、両方を直した。結果（2026-09-20、旧プロジェクトでの測定。計測 JSON と動画はこのリポジトリに持ち込んでいない）: 固定 9 試行の着地余裕は **修正前 −7.6〜0.7 mm → 修正後 7.2〜8.4 mm**（受け入れ値 ≥ 5 mm）、y = 0 の列（x 16〜23 cm × 両物体 × 両置き先、32 run）は **20/32 → 32/32**、掃引（固定 10 試行の規則を満たす開始点 94 run）は 90/94 → **94/94**（余裕最小 5.6 mm）、固定 5 試行 **5/5**、固定 fixture 4 本 4/4（拒否試験・停止の結果は不変）。

| 原因 | 直し方 | 効果（修正後の実測） |
|---|---|---|
| A. 手先の向きを「site の +Z（顎の開く方向）= 基部から径方向外向き」に固定していたので、yaw 0 で置かれた立方体が置き先の方位角（box_a +22°、box_b −22°）の分だけ回ったまま降りる。40 mm の立方体が 22° 回ると足元の半幅は 26 mm、内寸 60 mm のトレイでは片側 4 mm しか残らない | 顎の向きを **base 座標の yaw で指定**する規約に変更。pick は物体の登録 yaw（0）、place は置き先の yaw（トレイは軸平行、0）。真上把持では wrist_roll の軸が鉛直なので yaw は wrist_roll で自由に選べる | 立方体の辺とトレイ軸の差 17〜27° → **0.2〜2.1°** |
| B. 降下時に固定顎の衝突メッシュが立方体を顎軸方向へ約 6 mm 押すので、閉指令時の立方体は TCP から 29.6 mm（設計 23.5 mm）にある。置き TCP を設計値で決めると、この 6 mm がそのまま置き先の中心ずれになる（box_b では (+5.6, −2.2) mm、T2 実測 (+7.1, −2.5) mm） | 持ち上げ後に**立方体の tip-site 座標での位置を測り**、その顎軸成分で置き TCP を戻す（TCP = トレイ中心 − 実測オフセット × 顎開き方向） | 中心ずれ 3〜8 mm → **1.5〜2.5 mm** |

A だけでは中心ずれ 6 mm が残って余裕 4 mm、B だけでは回転が残って余裕 4 mm。両方で 7 mm 以上になった。

## 向きの規約（A）
- `top_down_target(position, yaw_deg=0.0)`: 接近軸（site 局所 +X）は world −Z、顎の開く方向（site 局所 +Z、固定顎の内面 → 可動顎）は base 座標で (cos yaw, sin yaw, 0)
- `solve_top_down(kin, position, yaw_deg=0.0)`: seed の shoulder_pan は 目標の方位角 × −1、wrist_roll は (yaw − 方位角) × +1（2026-09-20 実測: 真上姿勢で roll +30° → 顎の向き +30°、pan +30° → 手先 yaw −27°）。検査順は 目標 finite → 作業領域内 → 解 finite → 関節限界 → 位置残差 → 接近方向 → **顎 yaw**（`IkRejected("grasp_yaw")`）
- 許容値 `IK_GRASP_YAW_TOL_DEG = 2.0`（`so101_demo.tolerances`）: 40 mm 立方体の足元が 1.4 mm 広がる角度で、5 mm の余裕に対して十分小さい。実測の yaw 誤差は格子 100 点で 0.000°、yaw ±15 / ±30° の 400 点で最大 0.005°
- FK / IK の一致（2026-09-20）: FK 16 姿勢 位置 0.0033 mm / 姿勢 0.0009°（許容 1 mm / 1°）、真上把持 IK 100 点 拒否 0・残差 0.000 mm、同じ格子 × yaw −30 / −15 / +15 / +30° の 400 点 拒否 0・最悪 0.42 mm / 0.056° / 0.005°。今は `tests/test_kinematics_fk_agreement.py` と `tests/test_kinematics_top_down.py` が同じことを確認する
- 搬送の連続性: `_move_line` は TCP の直線とともに yaw も線形に補間する（pick の yaw → place の yaw。今は両方 0 なので wrist_roll は方位角の変化分だけ滑らかに回る）。関節速度は安全層（90 deg/s）が毎 tick 検査する。wrist_roll の範囲は ±157° 以上（MJCF）で、作業領域の方位角 ±35° に対して十分
- 影響: `grasp_tcp_for(centre, size, clearance, yaw_deg=0.0)` は顎軸に沿って TCP を基部側へ 23.5 mm 置く。yaw 0 では常に −x 方向。固定 10 試行の開始位置規則 `tcp`（`plan.manifest.start_violations`）はこの関数を物体の yaw で呼ぶ。現行 manifest（`contracts/fixtures/g1/manifest.json`）は規則を満たしたまま（`tests/test_g1_manifest.py`）

## 把持オフセットの補正（B）
- 測り方（sim）: `PickPlace.pick` が持ち上げ後に、位置取得（`PositionSource.pose_of`、sim では真値）で立方体中心を、**実測関節角の FK**（`kin.fk_mjcf_site`、実機でもエンコーダから得られる）で tip pose を得て、`held_offset_m = R_tipᵀ (p_cube − p_tip)` を保持する。固定 5 試行の実測: (0.0〜0.1, −0.1〜0.1, 29.5〜29.6) mm（設計 (0, 0, 23.5)）
- 妥当性検査: 実測値は**把持ゾーン**（`sim.grasp.GRASP_ZONE_*`、搬送監査 A と同じ箱）の内側にあるときだけ使う（`held_offset_is_plausible`）。把持を外した試行では持ち上げ後に机上の立方体を測ることになり、そのまま使うと置き目標が作業領域外になって `no_lift` が `rejected` に化ける（2026-09-20 に検出）。ゾーン外なら設計値 (0, 0, 23.5 mm) で置き、判定は判定器に任せる。使った値の出所は `held_offset_source`（`measured` / `design` / `fixed`）と trace の `pick:held_offset:<source>` に残る
- 使い方: `PickPlace.place` は TCP = トレイ中心 − R_place[:, Z] × held_offset[Z]。**顎軸（Z）成分だけ**を補正する。理由: 押されるのは顎軸方向であり、顎に直交する水平成分は 1 mm 未満のノイズで、しかもトレイ中心は作業領域の境界 y = ±0.09 にあるので横方向に 0.5 mm 動かせば置き TCP が領域外になって IK が拒否する（初版で実際に起きた）。z は従来どおり既知値（机面 + 半辺）で、鉛直成分（実測 ≈ 0）は使わない
- 固定値の経路（2026-09-20 の決定）: 実機の単眼カメラでは把持中の立方体を観測できない見込みなので、`PickPlace.measure_held_offset = False` で**固定値**を使う経路を用意した。固定値 = 設計ポケット位置 + 実測の押し量 `GRASP_PUSH_ALONG_JAW_M` = 6.1 mm（`fixed_held_offset_m`。`sim-v1-simple` では 23.5 + 6.1 = 29.6 mm、実測と一致）。課題では実機を扱わないので使わない。三つの経路は `tests/test_execution_pick_place.py` で確認する
- pick 側は変えない（降下時の 6 mm の押しはそのまま）。押しの原因（固定顎の衝突メッシュが site の Z = 0 面より約 9.6 mm 張り出す）は記録にとどめる

## 計測（`so101_demo.evidence.place_margin`）
- 着地余裕 = 底面 4 隅それぞれから最寄りの内壁までの距離の最小値（負 = 内寸の外）。中心ずれ = 静止時の中心 − トレイ中心。yaw 差 = 立方体の辺とトレイ軸の角度差（±45° に折り返し）。静止時刻は判定器の `settle_time`
- 3 群: 固定 9 試行（固定 5 試行は物体単独、固定 fixture 4 本は対象 home から・非対象 home）、掃引（固定 10 試行の規則 dist / tray / tcp を満たす 10 mm 格子 × 2 物体 × 2 置き先、両立方体配置）、y = 0 の列（x 16〜23 cm × 2 物体 × 2 置き先、対象単独）。すべて `run_trial`（`execution/trial.py`）で、実行器 `PlanRunner` の承認・リセット判定は通さない
- `--baseline --commit <hash>` で修正前のコードを先に計測し（`place-margin-before.json`）、修正後の既定実行がそれを読んで同じ表に並べる
- 掃引の結果（修正後）: 94 run 中 94 success、余裕最小 5.6 mm。初版では開始 (0.16, −0.09) の 4 run が `rejected`（`outside_workspace`）だった: 机上で静定した立方体の y が −0.09 より 2 × 10⁻¹¹ m 下にあり、yaw 0 では把持 TCP の y が立方体と同じなので境界判定に落ちる。対処は `workspace_contains` に **1 µm の丸め許容** `WORKSPACE_TOL_M` を足すこと（`joints_within_limits` の 1e-6 rad と同じ性格。領域の拡張ではない。`tests/test_kinematics_top_down.py` が 2e-11 は通し 1e-5 は拒否することを確認）。`_move_line` の最終 waypoint も `a + (b − a)` ではなく終点そのものを使うようにした（同じ境界上の目標が丸めで外に出ないため）

## 出典
- `src/so101_demo/kinematics/top_down.py`、`src/so101_demo/execution/primitives.py`、`src/so101_demo/evidence/place_margin.py`、`src/so101_demo/tolerances.py`
- `tests/test_execution_pick_place.py`、`tests/test_kinematics_top_down.py`、`tests/test_g1_manifest.py`
- `docs/g1-manifest.md`（固定 10 試行とその規則）
