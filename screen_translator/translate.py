"""翻译模块。"""

from __future__ import annotations

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


def translate_text(
    text: str,
    source: str = "auto",
    target: str = "zh-CN",
) -> str:
    """翻译文本。空文本原样返回。"""
    cleaned = text.strip()
    if not cleaned:
        return ""

    # GoogleTranslator 的 auto 源语言用 'auto'
    src = source if source else "auto"
    translator = GoogleTranslator(source=src, target=target)

    # 过长时分段，避免单次请求超限
    max_chunk = 4500
    if len(cleaned) <= max_chunk:
        return translator.translate(cleaned) or ""

    parts: list[str] = []
    buf: list[str] = []
    size = 0
    for line in cleaned.splitlines():
        extra = len(line) + 1
        if buf and size + extra > max_chunk:
            parts.append(translator.translate("\n".join(buf)) or "")
            buf = [line]
            size = len(line)
        else:
            buf.append(line)
            size += extra
    if buf:
        parts.append(translator.translate("\n".join(buf)) or "")
    return "\n".join(parts)