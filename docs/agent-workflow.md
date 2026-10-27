# エージェント環境の準備

関連: `AGENTS.md`（プロセスの正本）

Claude Code（メイン担当）と Codex（補助）を agmsg でつないで作業するための、マシンごとに 1 回の準備。`AGENTS.md` の運用はこれが済んでいる前提。

## 1. ツール
`git` `sqlite3` `node` `uv` `gh` と、`claude` `codex` の CLI。
```bash
sudo apt-get install -y git sqlite3 nodejs   # Ubuntu の例。gh は https://cli.github.com/
gh auth login                                # Issue の作成・コメントに必要
```
`node` は `codex-fresh.sh status` が app-server に載っている会話を照会するのに使う。無くても動くが、座席が古い会話を指しているか（STALE）を見分けられなくなる。

## 2. agmsg
```bash
git clone https://github.com/fujibee/agmsg.git && cd agmsg && ./install.sh   # または npx agmsg
```
`~/.agents/skills/agmsg/` に入り、Claude Code では `/agmsg`、Codex では `$agmsg` が使えるようになる。チームとエージェントの登録はこのリポジトリの `scripts/setup.sh` が行う（clone ごとに 1 回。チーム名はディレクトリ名）。

## 3. Codex の設定 `~/.codex/config.toml`
補助の Codex は agmsg のブリッジから動かされる。ブリッジが起こしたターンの承認要求は自動で却下されるので、必要な権限は先に開けておく。
```toml
sandbox_mode = "workspace-write"

[sandbox_workspace_write]
# agmsg が SQLite と状態ファイルを書く場所。~ は展開されないので絶対パスで書く
writable_roots = [
  "/home/<user>/.agents/skills/agmsg/db",
  "/home/<user>/.agents/skills/agmsg/teams",
  "/home/<user>/.agents/skills/agmsg/run",
]
# gh issue comment / gh issue view に必要
network_access = true
```
プロジェクトごとの `[projects."<path>"] trust_level = "trusted"` は `scripts/setup.sh` が追記する。リポジトリ直下の `.git` はこの設定でも書き込み不可のままだが、補助は git に書かない運用なので問題ない。

## 4. Claude Code
全体設定は不要。必要な設定はリポジトリ内の `.claude/settings.json`（`.env` の読み取り拒否、session-start フック）と、`scripts/setup.sh` が生成する `.claude/settings.local.json`（agmsg のフック）にある。Issue の起票・コメント（`scripts/issue.sh`）、ブランチの push、`main` への squash を Claude Code が行うので、それらを許可する権限モードで起動する。

## 5. 日々の使い方
1. `claude` でメイン担当を起動。session-start フックが未着手 Issue と現在のブランチを出す
2. 議論 → エージェントが Issue を起票し `/goal` 行を出す → 貼ると開始。複数まとめて進めるなら「#3 #4 を順にやって」
3. レビューが要る Issue を始める前に、別ターミナルで `scripts/codex-fresh.sh start`。1 作業セッションに 1 回。終わったら Codex を閉じて `scripts/codex-fresh.sh stop`
4. メインは READY を agmsg で送り、Codex が Issue に検証結果と指摘を書いて `OK` / `CHANGES` を返す。`OK` ならメインが `main` に squash して Issue を閉じる

## 前提と注意
- Codex は必ず `scripts/codex-fresh.sh start` で起動する。素の `codex` で開いた画面には agmsg のメッセージが届かない
- Codex を使わない間は `scripts/codex-fresh.sh stop`。止めないとブリッジが裏で動き続け、承認が要る操作をすべて却下しながら進める
- 別マシンで続けるときは `git clone` のあと `scripts/setup.sh` と `scripts/fetch-so101.sh` を実行する。同じチーム名が別のパスで既に登録されていると `setup.sh` は止まるので、`AGMSG_TEAM=<別名>` を付けて実行する
- 2 つのエージェントが同じ GitHub アカウントを使う。PR の approve は使わない
- commit の author はこの checkout の `git config`（GitHub の noreply アドレス）。clone ごとに設定し直す
