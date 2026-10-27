---
name: Task
about: エージェントの作業 1 単位。1 Issue = 作業 → 検証 → レビューの 1 ループ。
title: ""
labels: task
---

## ゴール
<!-- 完了したときに何が成り立っているか。成果物のファイル名まで書く（例: docs/report.md の「結論」節が埋まっている）。 -->

## 受け入れ条件
<!-- できる限り機械的に確認できるもの。「ユーザーが確認する」は書かない。 -->
- [ ]
- [ ]

## 検証
<!-- 実行するコマンドそのもの。verify-task スキルがこのブロックをそのまま実行する。コマンドが無い調査タスクでも、成果物の存在確認と git diff --check は書く。 -->
```
test -s docs/report.md
git diff --check
```

## 端末分担（AGENTS.md「Machine split」。GPU 機分がなければこの節を削る）
<!-- 分割する Issue では、上の「受け入れ条件」「検証」を part ごとに分けて書く。verify-task は現在の part のブロックだけを実行し、もう一方は「未達（<part>）」と記録する。part ごとに /goal と Started がある。 -->
### laptop 分（`task/<n>-<slug>`）
受け入れ条件:
- [ ]
検証:
```
git diff --check
```
### GPU 機分（`task/<n>-<slug>-gpu`。描画を伴う証拠、GPU 推論、提出構成の通し）
受け入れ条件:
- [ ]
検証:
```
git diff --check
```
### 引き渡す証拠
commit、設定 ID、実行手順、全試行ログ、既知の失敗

## 担当
<!-- 役割は固定（Claude Code がメイン、Codex が補助）。補助の中身だけ Issue ごとに選ぶ。AGENTS.md の Support を参照。 -->
- メイン: Claude Code
- 補助: Codex（`review` = コード・スクリプトの差分レビュー / `verify` = 文書の出典確認・反証・brief の未回答チェック）
- 補助モード: review

## 制限（AGENTS.md より）
- 検証ラウンド: 5
- レビュー往復: 3
- エージェントのターン数: 20（/goal 行に記載）
- 経過時間: /goal 1 回あたり 60 分、Issue あたり 2 日

## 補足
<!-- メイン担当が必要とする議論の背景。docs/brief.md や ADR へのリンク。 -->

## 開始
<!-- この行を Claude Code のセッションに貼ることが承認になる。<n> を Issue 番号に置き換える。 -->
このセッションで最初の Issue なら、貼る前に別ターミナルで `scripts/codex-fresh.sh start` を実行して補助の Codex を起動する（前の Codex 画面は先に閉じる）。
```
/goal Issue #<n> の受け入れ条件（分割 Issue では <laptop 分|GPU 機分> の条件）がすべて満たされ、verify-task が通り、ブランチが（origin があれば）push され、agmsg で READY を送っている。制約: 受け入れ条件を緩めない（修正が要るときは理由を Issue に記録する）。20ターンまたは60分で満たせなければ、試したこと・詰まっている点・代案を Issue にコメントして止まる
```
