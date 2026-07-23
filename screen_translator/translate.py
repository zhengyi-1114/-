"""翻译模块：支持 Google / GPT(OpenAI) / 豆包(火山方舟)。"""

from __future__ import annotations

import json
import os
import re
from typing import Optional

import requests
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

BACKENDS = {
    "google": "Google 翻译（免 Key）",
    "openai": "OpenAI / GPT（兼容接口）",
    "doubao": "豆包 / 火山方舟",
}

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

_LANG_NAME = {
    "auto": "自动检测的语言",
    "zh-CN": "简体中文",
    "zh-TW": "繁体中文",
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

HANGUL_RE = re.compile(r"[\uac00-\ud7a3]")
KANA_RE = re.compile(r"[\u3040-\u30ff]")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def normalize_lang(code: str) -> str:
    return _GOOGLE_CODE.get(code, code)


def guess_source_lang(text: str) -> str:
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


def resolve_backend(backend: Optional[str] = None) -> str:
    name = (backend or os.getenv("TRANSLATOR_BACKEND") or "google").strip().lower()
    if name in {"gpt", "chatgpt"}:
        name = "openai"
    if name in {"ark", "volc", "火山"}:
        name = "doubao"
    if name not in BACKENDS:
        raise ValueError(f"未知翻译后端: {backend}，可选: {', '.join(BACKENDS)}")
    return name


def _lang_label(code: str) -> str:
    return _LANG_NAME.get(code, code)


def _openai_compatible_chat(
    *,
    api_key: str,
    base_url: str,
    model: str,
    system: str,
    user: str,
    timeout: int = 120,
) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "temperature": 0.2,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
    if resp.status_code >= 400:
        raise RuntimeError(f"AI 翻译接口错误 {resp.status_code}: {resp.text[:500]}")
    data = resp.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"AI 翻译返回格式异常: {data}") from exc
    return str(content).strip()


def _ai_credentials(backend: str) -> tuple[str, str, str]:
    """返回 (api_key, base_url, model)。"""
    if backend == "doubao":
        api_key = (
            os.getenv("DOUBAO_API_KEY")
            or os.getenv("ARK_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or ""
        )
        base_url = os.getenv(
            "DOUBAO_BASE_URL",
            "https://ark.cn-beijing.volces.com/api/v3",
        )
        model = os.getenv("DOUBAO_MODEL") or os.getenv("OPENAI_MODEL") or ""
        if not api_key:
            raise RuntimeError(
                "豆包未配置 API Key。请设置环境变量 DOUBAO_API_KEY 或 ARK_API_KEY。"
            )
        if not model:
            raise RuntimeError(
                "豆包未配置模型。请设置 DOUBAO_MODEL 为方舟推理接入点 ID（如 ep-xxxxxxxx）。"
            )
        return api_key, base_url, model

    # openai 及一切兼容接口
    api_key = os.getenv("OPENAI_API_KEY") or ""
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    if not api_key:
        raise RuntimeError("未配置 OPENAI_API_KEY。")
    return api_key, base_url, model


def _translate_google(text: str, source: str, target: str) -> str:
    translator = GoogleTranslator(source=source, target=target)
    max_chunk = 4500
    if len(text) <= max_chunk:
        return (translator.translate(text) or "").strip()

    parts: list[str] = []
    buf: list[str] = []
    size = 0
    for line in text.splitlines():
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
    return "\n".join(parts).strip()


def _translate_ai(
    text: str,
    source: str,
    target: str,
    backend: str,
    paired: bool = False,
) -> str:
    api_key, base_url, model = _ai_credentials(backend)
    src_name = _lang_label(source)
    tgt_name = _lang_label(target)

    if paired:
        system = (
            f"你是专业的漫画/字幕翻译。把{src_name}译成{tgt_name}。"
            "用户会给出带编号的多行原文。你必须输出相同数量、相同编号的译文行。"
            "格式严格为：每行 `编号|译文`，不要输出其它说明。"
            "可轻微纠正明显 OCR 错字，但不要擅自增删台词含义；保留感叹号等语气。"
        )
        user = text
    else:
        system = (
            f"你是专业翻译。把{src_name}译成{tgt_name}。"
            "只输出译文，不要解释；保持换行结构与原文大致对应。"
            "若原文像 OCR 结果，可纠正明显错字后再翻译。"
        )
        user = text

    return _openai_compatible_chat(
        api_key=api_key,
        base_url=base_url,
        model=model,
        system=system,
        user=user,
    )


def translate_text(
    text: str,
    source: str = "auto",
    target: str = "zh-CN",
    backend: Optional[str] = None,
) -> str:
    """翻译整段文本。backend: google / openai / doubao。"""
    cleaned = text.strip()
    if not cleaned:
        return ""

    src = normalize_lang(source or "auto")
    tgt = normalize_lang(target or "zh-CN")
    if src == "auto":
        guessed = guess_source_lang(cleaned)
        if guessed != "auto":
            src = guessed
    if src == tgt:
        return cleaned

    engine = resolve_backend(backend)
    if engine == "google":
        return _translate_google(cleaned, src, tgt)
    return _translate_ai(cleaned, src, tgt, engine, paired=False)


def translate_lines(
    lines: list[str],
    source: str = "auto",
    target: str = "zh-CN",
    backend: Optional[str] = None,
    batch_size: int = 40,
) -> list[str]:
    """
    逐行翻译并尽量保持一一对应。
    AI 后端会按批次编号翻译；Google 则逐行调用。
    """
    cleaned = [ln.strip() for ln in lines]
    if not cleaned:
        return []

    src = normalize_lang(source or "auto")
    tgt = normalize_lang(target or "zh-CN")
    sample = "\n".join(ln for ln in cleaned if ln)[:500]
    if src == "auto" and sample:
        guessed = guess_source_lang(sample)
        if guessed != "auto":
            src = guessed

    engine = resolve_backend(backend)
    out: list[str] = [""] * len(cleaned)

    if engine == "google":
        for i, ln in enumerate(cleaned):
            if not ln:
                out[i] = ""
            elif src == tgt:
                out[i] = ln
            else:
                out[i] = _translate_google(ln, src, tgt)
        return out

    # AI：分批，编号对齐
    for start in range(0, len(cleaned), batch_size):
        chunk = cleaned[start : start + batch_size]
        indexed = []
        index_map: list[int] = []
        for offset, ln in enumerate(chunk):
            abs_i = start + offset
            if not ln:
                out[abs_i] = ""
                continue
            if src == tgt:
                out[abs_i] = ln
                continue
            indexed.append(f"{len(indexed) + 1}|{ln}")
            index_map.append(abs_i)

        if not indexed:
            continue

        raw = _translate_ai(
            "\n".join(indexed),
            src,
            tgt,
            engine,
            paired=True,
        )
        parsed: dict[int, str] = {}
        for row in raw.splitlines():
            row = row.strip()
            if not row:
                continue
            # 容忍 `1|译文` / `1. 译文` / `1、译文`
            m = re.match(r"^(\d+)\s*[|\.．、:：)\]]\s*(.*)$", row)
            if not m:
                continue
            parsed[int(m.group(1))] = m.group(2).strip()

        for local_i, abs_i in enumerate(index_map, start=1):
            out[abs_i] = parsed.get(local_i) or cleaned[abs_i]

    return out
