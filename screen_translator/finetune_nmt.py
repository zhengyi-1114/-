"""用韩中对照数据微调本地 NMT（NLLB）。

数据从哪来（任选）：
1) 双语对齐 JSON：ep-ref-align.json（字段 ko / zh_ref）
2) 对照文本：每条含「原文：」「汉化参考：」或「中文：」
3) JSONL：每行 {"source":"韩文","target":"中文"}

示例：
  # 准备数据
  python -m screen_translator.finetune_nmt prepare \\
    --input ep2-ref-align.json -o data/train.jsonl

  # 微调（CPU 很慢；有 GPU 会快很多）
  python -m screen_translator.finetune_nmt train \\
    --train data/train.jsonl -o models/nllb-ko-zh-ft --epochs 3

  # 从已有微调权重继续训（更小学习率）
  python -m screen_translator.finetune_nmt train \\
    --train data/train.jsonl -o models/nllb-ko-zh-ft \\
    --base-model models/nllb-ko-zh-ft --epochs 3 --lr 5e-6

  # 使用微调模型
  export TRANSLATOR_BACKEND=nmt
  export NMT_MODEL=/绝对路径/models/nllb-ko-zh-ft
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Iterable, Optional


SRC_LANG = "kor_Hang"
TGT_LANG = "zho_Hans"


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\n", " ")).strip()


def load_pairs_from_align_json(path: Path) -> list[tuple[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    pairs: list[tuple[str, str]] = []
    for row in data:
        src = _flat(row.get("ko") or row.get("source") or "")
        tgt = _flat(row.get("zh_ref") or row.get("target") or row.get("zh") or "")
        if len(src) < 2 or len(tgt) < 2:
            continue
        if tgt.startswith("[翻译失败"):
            continue
        pairs.append((src, tgt))
    return pairs


def load_pairs_from_paired_txt(path: Path) -> list[tuple[str, str]]:
    text = path.read_text(encoding="utf-8")
    pairs: list[tuple[str, str]] = []
    # 兼容：原文/韩文 + 汉化参考/中文
    blocks = re.split(r"\n(?=\d{3}\b)", text)
    for block in blocks:
        m_src = re.search(r"(?:原文|韩文)[:：]\s*(.+?)(?=\n(?:汉化参考|中文|机译)[:：]|\n\d{3}\b|\Z)", block, re.S)
        m_tgt = re.search(r"(?:汉化参考|中文)[:：]\s*(.+?)(?=\n(?:机译对照|机译)[:：]|\n\d{3}\b|\Z)", block, re.S)
        if not m_src or not m_tgt:
            continue
        src, tgt = _flat(m_src.group(1)), _flat(m_tgt.group(1))
        if len(src) >= 2 and len(tgt) >= 2:
            pairs.append((src, tgt))
    return pairs


def _pair_from_row(row: dict) -> Optional[tuple[str, str]]:
    src = _flat(row.get("source") or row.get("ko") or "")
    tgt = _flat(row.get("target") or row.get("zh") or row.get("zh_ref") or "")
    if len(src) >= 2 and len(tgt) >= 2:
        return src, tgt
    return None


def load_pairs_from_jsonl(path: Path) -> list[tuple[str, str]]:
    """支持标准 JSONL（一行一条），也支持缩进后的 JSON 数组。"""
    text = path.read_text(encoding="utf-8").strip()
    pairs: list[tuple[str, str]] = []
    if not text:
        return pairs
    # 缩进保存的 JSON 数组
    if text.startswith("["):
        data = json.loads(text)
        for row in data:
            if isinstance(row, dict):
                item = _pair_from_row(row)
                if item:
                    pairs.append(item)
        return pairs
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        item = _pair_from_row(row)
        if item:
            pairs.append(item)
    return pairs


def load_pairs(path: Path) -> list[tuple[str, str]]:
    suf = path.suffix.lower()
    if suf == ".jsonl":
        return load_pairs_from_jsonl(path)
    if suf == ".json":
        # align.json 或 source/target 数组均可
        text = path.read_text(encoding="utf-8").strip()
        if text.startswith("["):
            data = json.loads(text)
            if data and isinstance(data[0], dict) and (
                "source" in data[0] or "target" in data[0]
            ):
                pairs: list[tuple[str, str]] = []
                for row in data:
                    item = _pair_from_row(row)
                    if item:
                        pairs.append(item)
                return pairs
        return load_pairs_from_align_json(path)
    return load_pairs_from_paired_txt(path)


def write_jsonl(pairs: Iterable[tuple[str, str]], out: Path) -> int:
    out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with out.open("w", encoding="utf-8") as f:
        for src, tgt in pairs:
            f.write(json.dumps({"source": src, "target": tgt}, ensure_ascii=False) + "\n")
            n += 1
    return n


def cmd_prepare(args: argparse.Namespace) -> int:
    all_pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for p in args.input:
        path = Path(p)
        if not path.is_file():
            print(f"跳过（不存在）: {path}", file=sys.stderr)
            continue
        got = load_pairs(path)
        print(f"[prepare] {path} -> {len(got)} pairs", file=sys.stderr)
        for pair in got:
            if pair in seen:
                continue
            seen.add(pair)
            all_pairs.append(pair)
    if len(all_pairs) < 20:
        print(
            f"警告：有效句对只有 {len(all_pairs)} 条。微调至少建议 200+，更好 1000+。",
            file=sys.stderr,
        )
    n = write_jsonl(all_pairs, Path(args.output))
    print(Path(args.output))
    print(f"wrote {n} pairs", file=sys.stderr)
    return 0


def cmd_train(args: argparse.Namespace) -> int:
    """纯 PyTorch 微调循环（避免 Trainer/torchvision 兼容问题）。"""
    try:
        import random

        import torch
        from torch.utils.data import DataLoader, Dataset
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
    except ImportError as exc:
        print(
            "缺少依赖。请安装：pip install transformers torch sentencepiece",
            file=sys.stderr,
        )
        print(exc, file=sys.stderr)
        return 1

    train_path = Path(args.train)
    pairs = load_pairs_from_jsonl(train_path)
    if len(pairs) < 10:
        print("训练数据太少（<10），请先 prepare 更多对照。", file=sys.stderr)
        return 1

    val_ratio = max(0.0, min(0.2, float(args.val_ratio)))
    n_val = int(len(pairs) * val_ratio) if len(pairs) >= 50 else 0
    rng = random.Random(42)
    shuffled = list(pairs)
    rng.shuffle(shuffled)
    val_pairs = shuffled[:n_val]
    train_pairs = shuffled[n_val:] or shuffled

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 继续训练：--resume 时优先加载输出目录已有权重
    model_name = args.base_model
    if args.resume and (out_dir / "config.json").is_file():
        model_name = str(out_dir.resolve())
        print(f"[train] resume from {model_name}", file=sys.stderr)
    elif Path(model_name).is_dir() and (Path(model_name) / "config.json").is_file():
        model_name = str(Path(model_name).resolve())

    prev_meta: dict = {}
    meta_path = out_dir / "finetune_meta.json"
    if meta_path.is_file():
        try:
            prev_meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            prev_meta = {}

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    tok = AutoTokenizer.from_pretrained(model_name)
    if hasattr(tok, "src_lang"):
        tok.src_lang = SRC_LANG
    model = AutoModelForSeq2SeqLM.from_pretrained(model_name)
    model.to(device)
    model.train()

    class PairDataset(Dataset):
        def __init__(self, items: list[tuple[str, str]]):
            self.items = items

        def __len__(self) -> int:
            return len(self.items)

        def __getitem__(self, idx: int) -> tuple[str, str]:
            return self.items[idx]

    def collate(batch: list[tuple[str, str]]):
        sources = [a for a, _ in batch]
        targets = [b for _, b in batch]
        # NLLB 必须分别设置源/目标语种码，否则 labels 会错误地带上 src 前缀
        if hasattr(tok, "src_lang"):
            tok.src_lang = SRC_LANG
        if hasattr(tok, "set_src_lang_special_tokens"):
            tok.set_src_lang_special_tokens(SRC_LANG)
        enc = tok(
            sources,
            max_length=args.max_length,
            truncation=True,
            padding=True,
            return_tensors="pt",
        )
        if hasattr(tok, "tgt_lang"):
            tok.tgt_lang = TGT_LANG
        if hasattr(tok, "set_tgt_lang_special_tokens"):
            tok.set_tgt_lang_special_tokens(TGT_LANG)
        lab = tok(
            text_target=targets,
            max_length=args.max_length,
            truncation=True,
            padding=True,
            return_tensors="pt",
        )
        labels = lab["input_ids"]
        labels[labels == tok.pad_token_id] = -100
        enc["labels"] = labels
        return enc

    loader = DataLoader(
        PairDataset(train_pairs),
        batch_size=max(1, int(args.batch_size)),
        shuffle=True,
        collate_fn=collate,
    )
    optim = torch.optim.AdamW(model.parameters(), lr=float(args.lr), weight_decay=0.01)
    epochs = max(1, int(float(args.epochs)))
    grad_accum = max(1, int(args.grad_accum))
    print(
        f"[train] base={model_name} train={len(train_pairs)} val={len(val_pairs)} "
        f"device={device} epochs={epochs}",
        file=sys.stderr,
    )

    global_step = 0
    optim.zero_grad(set_to_none=True)
    for epoch in range(1, epochs + 1):
        running = 0.0
        steps = 0
        for i, batch in enumerate(loader, 1):
            batch = {k: v.to(device) for k, v in batch.items()}
            out = model(**batch)
            loss = out.loss / grad_accum
            loss.backward()
            running += float(out.loss.detach().cpu())
            steps += 1
            if i % grad_accum == 0 or i == len(loader):
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optim.step()
                optim.zero_grad(set_to_none=True)
                global_step += 1
                if global_step == 1 or global_step % 10 == 0:
                    avg = running / max(1, steps)
                    print(
                        f"[train] epoch={epoch}/{epochs} step={global_step} loss={avg:.4f}",
                        file=sys.stderr,
                        flush=True,
                    )
                    running = 0.0
                    steps = 0
        # 每个 epoch 存一次中间权重
        ckpt = out_dir / "checkpoints" / f"epoch-{epoch}"
        ckpt.mkdir(parents=True, exist_ok=True)
        model.save_pretrained(ckpt)
        tok.save_pretrained(ckpt)

    model.save_pretrained(out_dir)
    tok.save_pretrained(out_dir)
    history = list(prev_meta.get("history") or [])
    history.append(
        {
            "loaded_from": model_name,
            "epochs": epochs,
            "lr": float(args.lr),
            "batch_size": int(args.batch_size),
            "grad_accum": grad_accum,
            "train_size": len(train_pairs),
            "steps": global_step,
        }
    )
    meta = {
        "base_model": prev_meta.get("base_model") or model_name,
        "src_lang": SRC_LANG,
        "tgt_lang": TGT_LANG,
        "train_size": len(train_pairs),
        "val_size": len(val_pairs),
        "epochs": int(prev_meta.get("epochs") or 0) + epochs,
        "last_lr": float(args.lr),
        "device": str(device),
        "history": history,
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out_dir)
    print(
        "使用方式：\n"
        f"  export TRANSLATOR_BACKEND=nmt\n"
        f"  export NMT_MODEL={out_dir.resolve()}\n"
        f"  export NMT_ENGINE=nllb",
        file=sys.stderr,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="微调本地 NLLB 韩中翻译")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_prep = sub.add_parser("prepare", help="从对照文件生成 train.jsonl")
    p_prep.add_argument(
        "--input",
        "-i",
        nargs="+",
        required=True,
        help="align.json / 对照.txt / jsonl",
    )
    p_prep.add_argument("-o", "--output", default="data/train.jsonl")
    p_prep.set_defaults(func=cmd_prepare)

    p_tr = sub.add_parser("train", help="微调 NLLB")
    p_tr.add_argument("--train", required=True, help="train.jsonl")
    p_tr.add_argument("-o", "--output", default="models/nllb-ko-zh-ft")
    p_tr.add_argument(
        "--base-model",
        default="facebook/nllb-200-distilled-600M",
        help="基底模型",
    )
    p_tr.add_argument("--epochs", type=float, default=3.0)
    p_tr.add_argument("--batch-size", type=int, default=4)
    p_tr.add_argument("--grad-accum", type=int, default=4)
    p_tr.add_argument("--lr", type=float, default=1e-5)
    p_tr.add_argument("--max-length", type=int, default=128)
    p_tr.add_argument("--val-ratio", type=float, default=0.1)
    p_tr.add_argument(
        "--resume",
        action="store_true",
        help="从 -o 输出目录已有权重继续训练",
    )
    p_tr.add_argument("--fp16", action="store_true", help="GPU 半精度")
    p_tr.set_defaults(func=cmd_train)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
