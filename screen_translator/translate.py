"""翻译模块。"""

from __future__ import annotations

import re

from deep_translator import GoogleTranslator

# 常用语言代码 → 显示名
LANGUAGES: dict[str, str] = {
    "auto": "自动检测",
    "zh-CN": "中文（简体）",
    "zh-TW": "中文（繁体）",
    "en": "英语",
    "ja": "日语",
    "ko": "韩语",
    "fr": "法语",
    "de": "德语",
    "es": "西班牙语",
    "ru": "俄语",
    "pt": "葡萄牙语",
    "vi": "越南语",
    "th": "泰语",
}

# deep-translator / Google 支持的代码规范化
_GOOGLE_CODE = {
    "auto": "auto",
    "zh": "zh-CN",
    "zh-cn": "zh-CN",
    "zh-CN": "zh-CN",
    "zh-tw": "zh-TW",
    "zh-TW": "zh-TW",
    "en": "en",
    "ja": "ja",
    "jp": "ja",
    "ko": "ko",
    "kr": "ko",
    "fr": "fr",
    "de": "de",
    "es": "es",
    "ru": "ru",
    "pt": "pt",
    "vi": "vi",
    "th": "th",
}

HANGUL_RE = re.compile(r"[\uac00-\ud7a3]")
KANA_RE = re.compile(r"[\u3040-\u30ff]")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def normalize_lang(code: str) -> str:
    return _GOOGLE_CODE.get(code, code)


def guess_source_lang(text: str) -> str:
    """从文本猜测源语言，避免 auto 对韩文误判。"""
    hangul = len(HANGUL_RE.findall(text))
    kana = len(KANA_RE.findall(text))
    cjk = len(CJK_RE.findall(text))
    latin = len(re.findall(r"[A-Za-z]", text))
    if hangul >= 2 and hangul >= kana and hangul >= cjk // 2:
        return "ko"
    if kana >= 2:
        return "ja"
    if cjk >= 2 and cjk >= latin // 2:
        return "zh-CN"
    if latin >= 8:
        return "en"
    return "auto"


def translate_text(
    text: str,
    source: str = "auto",
    target: str = "zh-CN",
) -> str:
    """翻译文本。空文本原样返回。"""
    cleaned = text.strip()
    if not cleaned:
        return ""

    src = normalize_lang(source or "auto")
    tgt = normalize_lang(target or "zh-CN")

    # auto 时按文字体系猜测，韩文尤其需要明确 source=ko
    if src == "auto":
        guessed = guess_source_lang(cleaned)
        if guessed != "auto":
            src = guessed

    if src == tgt:
        return cleaned

    translator = GoogleTranslator(source=src, target=tgt)

    max_chunk = 4500
    if len(cleaned) <= max_chunk:
        result = translator.translate(cleaned)
        return (result or "").strip()

    parts: list[str] = []
    buf: list[str] = []
    size = 0
    for line in cleaned.splitlines():
        extra = len(line) + 1
        if buf and size + extra > max_chunk:
            chunk = translator.translate("\n".join(buf)) or ""
            parts.append(chunk)
            buf = [line]
            size = len(line)
        else:
            buf.append(line)
            size += extra
    if buf:
        parts.append(translator.translate("\n".join(buf)) or "")
    return "\n".join(parts).strip()
