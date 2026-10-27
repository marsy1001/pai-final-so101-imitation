# 0001. No separate video script document

- Status: Proposed
- Date: 2026-10-27

## Context
`docs/brief.md` は成果物に `docs/video-script.md`（場面、字幕、尺、倍速の有無）を挙げていたが、課題の提出物は動画・コード・レポートの 3 点で、台本は採点対象ではない。場面構成は `docs/report.md` の「提出動画の構成」節（5 場面）にすでにあり、動画の撮影と編集はそれで進められる。締切は 2026-11-02 10:00 で、台本を独立した文書として書き、補助の `verify` を通す時間は動画とレポートの仕上げに回したい。

## Decision
動画の台本は独立した文書として作らない。場面構成、字幕の文言、各場面の秒数、倍速の有無は `docs/report.md` の「提出動画の構成」節に書き、レポートの一部として確認する。

## Consequences
- `docs/brief.md`、`README.md`、`AGENTS.md` から `docs/video-script.md` への言及を外す（本 ADR と同じコミット）
- 字幕の文言と数字がレポートと矛盾しないことは、レポートの `verify` で確認する
- 動画を上げた後に字幕を直すのは難しいので、字幕を付ける前にその文言をレポートの節に入れて確認する
