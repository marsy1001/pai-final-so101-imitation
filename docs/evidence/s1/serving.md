# S1: Cosmos Reason 2 2B を vLLM で起動する手順と実測（母艦）

GPU 機での実測、実測日 2026-09-28。構成は別 uv プロジェクト `serving/cosmos/` に置いた（本体の `uv.lock` と vLLM の依存を分けるため）。

## 結論
- `serving/cosmos/serve.sh` 1 本で起動できる。引き継ぎと同じフラグで、vLLM 0.12.0 / torch 2.9.0+cu128 / transformers 4.57.1 を `serving/cosmos/uv.lock` で固定した
- 起動所要（`serve.sh` の実行から `/health` が 200 を返すまで）:
  - この環境で初めての起動（torch.compile のキャッシュが無い状態）: **78.1 秒**
  - 2 回目（キャッシュあり）: **44.2 秒**
- 常駐 VRAM:
  - 起動直後: **11,785 MiB / 12,009 MiB**（1 回目 / 2 回目）
  - 動画の推論を 1 回通した後: **13,997 MiB / 13,849 MiB** に増え、その後は増えない
  - 空きは最小で **2,052 MiB**（搭載 16,376 MiB）
- 起動後の初回推論（ウォームアップ）: **19.31 秒 / 19.18 秒**（10 秒動画 1 本）。ウォームアップ直後の同じ動画は 2.17 秒
- 生成速度は **約 115 tokens/s**。`ignore_eos` で 1024 トークン生成に 9.39 秒、16 トークン生成に 0.61 秒かかり、差分から求めた。`max_tokens` の根拠に使う（`diagnosis.md`「運用値」）
- VRAM に余裕は無い。起動前に `nvidia-smi` で空きを確かめる（手順 2）。sim の描画を同じ GPU に同時に載せるには、この約 2 GiB の空きに収める必要がある。同時常駐のピークは第 3 段で測る

## 手順
1. 一度だけ行う準備: `cd serving/cosmos && uv sync --frozen`（vLLM と torch の wheel で数 GB）。モデルの重み（4.24 GiB）は、初回起動時に Hugging Face から `~/.cache/huggingface/hub` へ取得される
2. 起動前: `nvidia-smi --query-gpu=memory.used,memory.free --format=csv`。`--gpu-memory-utilization 0.85` は GPU を独占する前提で、空きが 14 GiB を切っていたら、Windows 側で VRAM を使っているアプリを閉じてから起動する（今回の起動前は used 481 MiB / free 15,568 MiB）
3. 起動: `serving/cosmos/serve.sh`。ポートを変えるときは `PORT=<n>` を付ける。ログに `Application startup complete.` が出て、`curl -sf localhost:8000/health` が 0 を返せば準備完了
4. ウォームアップ: 動画を 1 本推論する（約 19 秒）。`uv run python -m so101_demo.evidence.s1_diagnosis` は最初にこれを行い、計測から外す
5. 停止: `serve.sh` のプロセスを終了（Ctrl-C または `kill <pid>`）。VRAM は数秒で起動前の値に戻る（481 MiB）

`serve.sh` のフラグ:

```
vllm serve nvidia/Cosmos-Reason2-2B --allowed-local-media-path <repo> --max-model-len 16384 \
  --media-io-kwargs '{"video": {"num_frames": -1}}' --reasoning-parser qwen3 \
  --limit-mm-per-prompt '{"image": 1, "video": 1}' --gpu-memory-utilization 0.85 \
  --max-num-seqs 1 --port 8000
```

`--allowed-local-media-path` はリポジトリの root にした。クライアントは動画を `file://` の絶対パスで渡す（`src/so101_demo/understanding/cosmos.py`）。

## 実測の詳細

| 項目 | 1 回目の起動 | 2 回目の起動 |
|---|---|---|
| 起動前の `nvidia-smi` | used 481 / free 15,568 MiB | used 481 / free 15,568 MiB |
| 重みのロード | 4.24 GiB、6.1 秒 | 4.24 GiB、4.5 秒 |
| torch.compile | 31.35 秒（キャッシュ無し） | 9.42 秒 |
| engine の初期化（profile、KV キャッシュ、warmup） | 41.15 秒 | 15.64 秒 |
| KV キャッシュ | 6.32 GiB、59,168 トークン（16,384 トークン要求の 3.61 倍） | 6.55 GiB、61,328 トークン（3.74 倍） |
| `/health` が 200 になるまで | 78.1 秒 | 44.2 秒 |
| 起動直後の `nvidia-smi` | used 11,785 / free 4,264 MiB | used 12,009 / free 4,040 MiB |
| 初回推論（s1-01、ウォームアップ） | 19.31 秒（s1-01） | 19.18 秒（s1-01） |
| 推論後の `nvidia-smi` | used 13,997 / free 2,052 MiB | used 13,849 / free 2,200 MiB |

- 推論中の VRAM: 1 回目の起動で、s1-03 の推論（3.82 秒）の間 0.5 秒ごとに 40 回 `nvidia-smi` を取った。値は 40 回とも 13,997 MiB で、最初の動画推論で確保した分から増えない
- 動画 1 本の入力: 40 フレーム × 1056×576 で、動画トークン 11,880、プロンプト合計 12,151 トークン（`diagnosis.md`。容器の問いを外す前の指示文では 12,173）。KV キャッシュ（59,168 / 61,328 トークン）に収まる
- 警告: `Using 'pin_memory=False' as WSL is detected.` が出る（引き継ぎと同じ）

## 環境（リポジトリの外に置くもの）
- マシン: 母艦（Alienware、RTX 4090 Laptop 16,376 MiB）。WSL2（kernel 6.6.87.2-microsoft-standard-WSL2）、RAM 15 GiB。CUDA Toolkit と Docker は入れていない（wheel 同梱のランタイムで動く）
- C コンパイラ: Zig の `cc` シム（`~/.local/zigcc/cc`、`c++`、`env.sh`、ziglang 0.16.0、`~/.local/zigenv`）。`serve.sh` は `~/.local/zigcc/env.sh` があれば source する。無い場合は、Zig の `zig cc` を `cc` / `c++` 名で呼ぶシェルスクリプトを置き、`env.sh` で PATH の先頭に足す。シムが無いと、Triton の JIT で `RuntimeError: Failed to find C compiler.` が出て起動に失敗する
- キャッシュ: `~/.cache/vllm`（今回 426 MB）。消えると torch.compile が 31 秒かかる初回の状態に戻る

## 出典
- 2026-09-15 の旧プロジェクトでの実測（記録は持ち込んでいない）: 常駐 11,528 MiB、初回推論 17.9〜20.0 秒
- `serving/cosmos/serve.sh`、`serving/cosmos/pyproject.toml`
- 生ログはリポジトリの外（`tmp/s1-serving/`、git 管理外）
