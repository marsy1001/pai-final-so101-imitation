# RobotBackend と位置取得の契約（sim / 実機の差し替え境界）

関連: [[demo-plan]]「sim の設計」、`docs/handover/mujoco-so101-notes.md` 6 節、Issue #4（2026-09-19）

## 結論
実行層（`so101_demo.execution`）と機体の間に **2 つの境界**を置く。(1) `RobotBackend`（関節指令・グリッパー・状態・停止・単一の指令供給元）、(2) `PositionSource`（登録 ID → pose）。sim 実装は `robot.MujocoBackend` と `locate.SimGroundTruth`、実機（第 4 段）は同じ Protocol の別実装に差し替える。**RobotBackend は IK を呼ばない**。関節目標は実行層が `kinematics` で作り、`safety` を通してから渡す。

## 1. RobotBackend（`src/so101_demo/robot/backend.py`）
| 項目 | 契約 |
|---|---|
| 関節順序 | 5 軸固定 `("shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll")`（`frames.JOINT_NAMES`）。名前で対応付け、ファイル内の列挙順に依存しない |
| 単位 | 関節は **度**。backend 内部で rad へ変換（`units.deg_to_rad`）。長さは m |
| グリッパー | **0〜100**（LeRobot `RANGE_0_100`、0 = 閉、100 = 開）。sim では MJCF `gripper` 角度へ線形写像: 0 → −10°、100 → +100°（2026-09-19 実測端点: −10° で顎の隙間 1.9 mm、100° で全開）。範囲外はクリップし外挿しない。実機は第 4 段で較正し直す |
| 状態 | `state()` → `RobotState(joints_deg, gripper, time_s, moving)`。sim では qpos から読む（指令値ではない） |
| 停止 | `stop(token)`: 関節目標 := 現在値、グリッパー目標 := 現在値。**保持中の物体は保持したまま**（落とすより安全）。待機中の動作は実行層が同じ呼び出しで破棄する（`PickPlace.stop`） |
| リセット | `reset()`: 時刻・関節・速度・制御・保持状態・指令ログを初期化しホーム姿勢へ。物体の配置は所有者（`execution.trial`）が行い、その後 1 s 静止。所有トークンも消える |
| 単一の指令供給元 | `acquire(owner)` で `CommandToken` を得た 1 者だけが指令できる。別 owner の `acquire`、他人 / 失効トークンでの指令は `NotCommandOwner`。`release(token)` で手放す |
| 進行 | `tick(dt_s)`: sim を dt 分進める。実機では待機に相当 |

## 2. PositionSource（`src/so101_demo/locate/source.py`）
- `pose_of(object_id) -> ObjectPose(position_m, rotation, linear_velocity, angular_velocity, time_s)`（base 座標）
- sim = `SimGroundTruth`（MuJoCo の body pose）。実機 = 俯瞰カメラ検出 + 座標変換（単層・既知高さ）。**置き先は固定位置**なので `contracts/registry.json` から読み、位置取得の対象ではない
- 実行層は x, y だけを位置取得から使い、把持高さは机面 + 物体高さ / 2 の既知値（handover notes 7 節）。実機の単一カメラでは z が観測できないため

## 3. 実行層の責務（境界の外側）
- `execution.safety`: 作業領域（`kinematics.top_down`）、関節限界、関節速度上限（90 deg/s）、単一供給元。違反は `SafetyRejected`、クリップしない
- `execution.primitives`: pick = 上空 → 降下 → 閉 → 持ち上げ、place = 搬送 → 降下 → 開 → 退避。直線区間は IK を 8 分割で解き、関節を 50 Hz で線形補間
- `execution.judge`: 0b の合否（`criteria.py` の固定値）。保持リストは読まない

## 4. 差し替えの手順（第 4 段）
1. `robot/so101_backend.py` に同じ Protocol を実装（LeRobot `so_follower`、5 軸順序と 0〜100 をそのまま）
2. `locate/camera.py` に `PositionSource` を実装（検出 + 変換）
3. `execution` は変更しない。`trial.run_trial` の構築部分だけ実装を差し替える
- sim 5/5 は実機成功の証拠にならない。実機は demo-plan 第 4 段の独立した受け入れ（安全・成功率）を通す
