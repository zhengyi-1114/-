#!/usr/bin/env bash
set -euo pipefail
cd /workspace
PY=/workspace/.venv/bin/python
export PATH="/workspace/.venv/bin:$HOME/.local/bin:$PATH"
export HF_HOME=/workspace/.cache/huggingface
LOG=/workspace/logs/continue_train_ep091.log
rm -f /workspace/logs/continue_train_ep091.status
: > "$LOG"
exec > >(tee -a "$LOG") 2>&1

echo "==== $(date -Is) continue from FT on part091-116 ===="
echo "oversample=$(wc -l < data/train.oversample.jsonl) train=$(wc -l < data/train.jsonl)"

if [[ ! -f models/nllb-ko-zh-ft/config.json ]]; then
  echo "missing FT model, training from base"
  BASE=facebook/nllb-200-distilled-600M
  RESUME_FLAG=()
else
  BASE=models/nllb-ko-zh-ft
  RESUME_FLAG=(--resume)
fi

$PY -m screen_translator.finetune_nmt train \
  --train data/train.oversample.jsonl \
  -o models/nllb-ko-zh-ft \
  --base-model "$BASE" \
  "${RESUME_FLAG[@]}" \
  --epochs 4 \
  --batch-size 2 \
  --grad-accum 8 \
  --lr 1e-5 \
  --max-length 128 \
  --val-ratio 0.05

echo "==== Eval ===="
$PY - << 'PY'
import os
from pathlib import Path
os.environ["TRANSLATOR_BACKEND"]="nmt"
os.environ["NMT_ENGINE"]="nllb"
os.environ["NMT_MODEL"]=str(Path("models/nllb-ko-zh-ft").resolve())
from screen_translator.nmt import translate_nmt
cases=[
"드디어 풀었다.",
"계좌 비밀번호를...",
"천성호",
"성장의 마족인가.",
"기억탐색 Lv.10 발동",
"무재조정(EX)",
"못 갑니다.",
"택시비가 없습니다.",
"운명적 만남",
"회풍불류검법",
]
for s in cases:
    print(f"{s} => {translate_nmt(s,'ko','zh-CN')}")
PY

cat > /workspace/.env << ENV
TRANSLATOR_BACKEND=nmt
NMT_ENGINE=nllb
NMT_MODEL=/workspace/models/nllb-ko-zh-ft
ENV
echo DONE > /workspace/logs/continue_train_ep091.status
echo "==== $(date -Is) DONE ===="
