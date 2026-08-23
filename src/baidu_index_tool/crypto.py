from __future__ import annotations

from typing import Dict, List, Optional, Union


class DecryptionError(ValueError):
    pass


def build_translation_table(ptbk: str) -> Dict[str, str]:
    """Build the substitution table returned by the ptbk endpoint."""
    if not isinstance(ptbk, str) or not ptbk:
        raise DecryptionError("ptbk 为空")
    if len(ptbk) % 2:
        raise DecryptionError("ptbk 长度必须为偶数")
    half = len(ptbk) // 2
    left, right = ptbk[:half], ptbk[half:]
    if len(set(left)) != len(left):
        raise DecryptionError("ptbk 左半段存在重复字符")
    return dict(zip(left, right))


def decrypt_text(ciphertext: str, ptbk: str) -> str:
    if ciphertext in (None, ""):
        return ""
    table = build_translation_table(ptbk)
    missing = sorted(set(ciphertext) - set(table))
    if missing:
        raise DecryptionError("密文包含 ptbk 未定义字符: %r" % missing[:8])
    return "".join(table[ch] for ch in ciphertext)


def _number(value: str) -> Optional[Union[int, float]]:
    value = value.strip()
    # Inside a comma-separated Baidu index series an empty slot denotes zero.
    # A completely empty series is still handled as unavailable by
    # ``decrypt_series`` before this function is called.
    if value == "":
        return 0
    if value in ("-", "--", "null", "None"):
        return None
    try:
        return int(value)
    except ValueError:
        try:
            return float(value)
        except ValueError as exc:
            raise DecryptionError("指数值格式异常: %r" % value) from exc


def decrypt_series(ciphertext: str, ptbk: str) -> List[Optional[Union[int, float]]]:
    plain = decrypt_text(ciphertext or "", ptbk)
    if plain == "":
        return []
    return [_number(item) for item in plain.split(",")]
