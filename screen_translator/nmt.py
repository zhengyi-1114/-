"""本地深度学习神经机器翻译（NMT），不调用大模型 API。

默认优先：
1) facebook/nllb-200-distilled-600M（多语 NMT，支持韩↔中直译）
2) 回退 MarianMT：Helsinki-NLP/opus-mt-ko-en + opus-mt-en-zh（经英语中转）
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import Optional

# NLLB 语言码
_NLLB_LANG = {
    "ko": "kor_Hang",
    "zh-CN": "zho_Hans",
    "zh": "zho_Hans",
    "zh-TW": "zho_Hant",
    "en": "eng_Latn",
    "ja": "jpn_Jpan",
    "fr": "fra_Latn",
    "de": "deu_Latn",
    "es": "spa_Latn",
    "ru": "rus_Cyrl",
    "pt": "por_Latn",
    "vi": "vie_Latn",
    "th": "tha_Thai",
}

_MARIAN_DIRECT = {
    ("en", "zh-CN"): "Helsinki-NLP/opus-mt-en-zh",
    ("en", "zh"): "Helsinki-NLP/opus-mt-en-zh",
    ("zh-CN", "en"): "Helsinki-NLP/opus-mt-zh-en",
    ("zh", "en"): "Helsinki-NLP/opus-mt-zh-en",
    ("ko", "en"): "Helsinki-NLP/opus-mt-ko-en",
    ("en", "ko"): "Helsinki-NLP/opus-mt-en-ko",
    ("ja", "en"): "Helsinki-NLP/opus-mt-ja-en",
    ("en", "ja"): "Helsinki-NLP/opus-mt-en-jap",
}


def _device() -> str:
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
        if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


@lru_cache(maxsize=2)
def _load_nllb(model_name: str):
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForSeq2SeqLM.from_pretrained(model_name)
    model.to(_device())
    model.eval()
    return tok, model


@lru_cache(maxsize=8)
def _load_marian(model_name: str):
    from transformers import MarianMTModel, MarianTokenizer

    tok = MarianTokenizer.from_pretrained(model_name)
    model = MarianMTModel.from_pretrained(model_name)
    model.to(_device())
    model.eval()
    return tok, model


def _chunk_text(text: str, max_chars: int = 400) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    parts: list[str] = []
    buf: list[str] = []
    size = 0
    for line in text.splitlines() or [text]:
        extra = len(line) + 1
        if buf and size + extra > max_chars:
            parts.append("\n".join(buf))
            buf = [line]
            size = len(line)
        else:
            buf.append(line)
            size += extra
    if buf:
        parts.append("\n".join(buf))
    return parts


def _nllb_translate(text: str, source: str, target: str, model_name: str) -> str:
    import torch

    src = _NLLB_LANG.get(source, source)
    tgt = _NLLB_LANG.get(target, target)
    tok, model = _load_nllb(model_name)
    # transformers 新版本用 src_lang
    if hasattr(tok, "src_lang"):
        tok.src_lang = src
    outs: list[str] = []
    for chunk in _chunk_text(text):
        encoded = tok(chunk, return_tensors="pt", truncation=True, max_length=512)
        encoded = {k: v.to(model.device) for k, v in encoded.items()}
        forced_bos = tok.convert_tokens_to_ids(tgt)
        with torch.no_grad():
            generated = model.generate(
                **encoded,
                forced_bos_token_id=forced_bos,
                max_new_tokens=256,
                num_beams=4,
                max_length=None,
            )
        outs.append(tok.batch_decode(generated, skip_special_tokens=True)[0].strip())
    return "\n".join(outs).strip()


def _marian_once(text: str, model_name: str) -> str:
    import torch

    tok, model = _load_marian(model_name)
    outs: list[str] = []
    for chunk in _chunk_text(text):
        encoded = tok(chunk, return_tensors="pt", truncation=True, padding=True, max_length=512)
        encoded = {k: v.to(model.device) for k, v in encoded.items()}
        with torch.no_grad():
            generated = model.generate(
                **encoded, max_new_tokens=256, num_beams=4, max_length=None
            )
        outs.append(tok.batch_decode(generated, skip_special_tokens=True)[0].strip())
    return "\n".join(outs).strip()


def _marian_translate(text: str, source: str, target: str) -> str:
    """MarianMT：有直达模型则直译，否则经英语中转。"""
    key = (source, target)
    if key in _MARIAN_DIRECT:
        return _marian_once(text, _MARIAN_DIRECT[key])

    # 常见：ko -> zh 经 en
    if source != "en" and (source, "en") in _MARIAN_DIRECT:
        mid = _marian_once(text, _MARIAN_DIRECT[(source, "en")])
    elif source == "en":
        mid = text
    else:
        raise RuntimeError(f"MarianMT 不支持源语言: {source}")

    if target == "en":
        return mid
    if ("en", target) in _MARIAN_DIRECT:
        return _marian_once(mid, _MARIAN_DIRECT[("en", target)])
    raise RuntimeError(f"MarianMT 不支持目标语言: {target}")


def translate_nmt(
    text: str,
    source: str = "ko",
    target: str = "zh-CN",
    *,
    engine: Optional[str] = None,
) -> str:
    """
    本地 NMT 翻译。

    engine:
      - auto / nllb: NLLB（默认）
      - marian: MarianMT（opus-mt）
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return ""

    # 归一化
    src = source
    tgt = target
    if src in {"zh", "zh-cn", "zh-CN"}:
        src = "zh-CN"
    if tgt in {"zh", "zh-cn", "zh-CN"}:
        tgt = "zh-CN"
    if src == "auto":
        if re.search(r"[\uac00-\ud7a3]", cleaned):
            src = "ko"
        elif re.search(r"[\u3040-\u30ff]", cleaned):
            src = "ja"
        elif re.search(r"[A-Za-z]", cleaned):
            src = "en"
        else:
            src = "ko"
    if src == tgt:
        return cleaned

    mode = (engine or os.getenv("NMT_ENGINE") or "auto").strip().lower()
    model_name = os.getenv("NMT_MODEL", "facebook/nllb-200-distilled-600M")

    errors: list[str] = []
    if mode in {"auto", "nllb"}:
        try:
            return _nllb_translate(cleaned, src, tgt, model_name)
        except Exception as exc:
            errors.append(f"nllb: {exc}")
            if mode == "nllb":
                raise RuntimeError(f"NLLB 翻译失败: {exc}") from exc

    if mode in {"auto", "marian"}:
        try:
            return _marian_translate(cleaned, src, tgt)
        except Exception as exc:
            errors.append(f"marian: {exc}")
            if mode == "marian":
                raise RuntimeError(f"MarianMT 翻译失败: {exc}") from exc

    raise RuntimeError("本地 NMT 翻译失败: " + " | ".join(errors))
