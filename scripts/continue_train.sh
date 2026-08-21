#!/usr/bin/env bash
set -euo pipefail
cd /workspace
PY=/workspace/.venv/bin/python
export PATH="/workspace/.venv/bin:$HOME/.local/bin:$PATH"
export HF_HOME=/workspace/.cache/huggingface
mkdir -p /workspace/logs /workspace/models "$HF_HOME"
rm -f /workspace/logs/continue_train.status
LOG=/workspace/logs/continue_train.log
: > "$LOG"
exec > >(tee -a "$LOG") 2>&1

echo "==== $(date -Is) START retrain (tgt_lang fix + oversample) ===="
echo "pairs=$(wc -l < data/train.oversample.jsonl)"

# Fresh from base — previous run used wrong label lang tokens
rm -rf models/nllb-ko-zh-ft

echo "==== Phase1: base 4 epochs lr=2e-5 oversample ===="
$PY -m screen_translator.finetune_nmt train \
  --train data/train.oversample.jsonl \
  -o models/nllb-ko-zh-ft \
  --base-model facebook/nllb-200-distilled-600M \
  --epochs 4 \
  --batch-size 2 \
  --grad-accum 8 \
  --lr 2e-5 \
  --max-length 128 \
  --val-ratio 0.05

echo "==== Phase2: resume 3 epochs lr=5e-6 ===="
$PY -m screen_translator.finetune_nmt train \
  --train data/train.oversample.jsonl \
  -o models/nllb-ko-zh-ft \
  --resume \
  --epochs 3 \
  --batch-size 2 \
  --grad-accum 8 \
  --lr 5e-6 \
  --max-length 128 \
  --val-ratio 0.05

echo "==== Eval smoke ===="
$PY - << 'PY'
from pathlib import Path
import os
os.environ["TRANSLATOR_BACKEND"] = "nmt"
os.environ["NMT_ENGINE"] = "nllb"
os.environ["NMT_MODEL"] = str(Path("/workspace/models/nllb-ko-zh-ft").resolve())
# clear lru cache by fresh import process
from screen_translator.nmt import translate_nmt
cases = [
    "회풍불류검법",
    "훌륭한 쾌검이군.",
    "이놈들은 바보같이 방심하다 당햇군.",
    "이집 셋째",
    "파협검법",
    "만귀혈검법",
    "금강불괴체",
]
for s in cases:
    print(f"{s} => {translate_nmt(s, 'ko', 'zh-CN')}")
PY

cat > /workspace/.env << ENV
TRANSLATOR_BACKEND=nmt
NMT_ENGINE=nllb
NMT_MODEL=/workspace/models/nllb-ko-zh-ft
ENV

echo "==== $(date -Is) DONE ===="
echo DONE > /workspace/logs/continue_train.status
