from __future__ import annotations

import os
from pathlib import Path

# The .env the bot reads. Overridable for tests/alternate deployments.
ENV_PATH = Path(os.getenv("BOT_ENV_FILE", ".env"))


def _inline_comment_index(value_part: str) -> int | None:
    """Index of an inline ``#`` comment in the value portion of a KEY=VALUE line.

    python-dotenv treats a ``#`` as an inline comment only when it follows
    whitespace (so a literal ``#`` glued to the value is kept). We mirror that so
    rewriting a line never eats a legitimate ``#`` inside a token.
    """
    pos = value_part.find(" #")
    return pos + 1 if pos != -1 else None


def _unescape(s: str) -> str:
    """Undo the escaping applied to double-quoted values (mirrors python-dotenv)."""
    out: list[str] = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            out.append({"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}.get(nxt, nxt))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _strip_value(value_part: str) -> str:
    """Turn the right-hand side of a KEY=VALUE line into its effective value.

    Quoted values keep everything up to the matching closing quote (so a ``#`` or
    spaces inside are preserved); double-quoted values are unescaped (``\\n`` ->
    newline). Unquoted values drop an inline ``# comment`` and surrounding space.
    """
    vp = value_part.strip()
    if vp and vp[0] in ("'", '"'):
        quote = vp[0]
        end = vp.find(quote, 1)
        if end != -1:
            inner = vp[1:end]
            return _unescape(inner) if quote == '"' else inner
    idx = _inline_comment_index(value_part)
    if idx is not None:
        value_part = value_part[:idx]
    return value_part.strip()


def read_env(path: Path | str = ENV_PATH) -> dict[str, str]:
    """Parse a .env file into {KEY: value}, ignoring comments and blank lines."""
    p = Path(path)
    out: dict[str, str] = {}
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in line:
            continue
        key, value_part = line.split("=", 1)
        out[key.strip()] = _strip_value(value_part)
    return out


def update_env(updates: dict[str, str], path: Path | str = ENV_PATH) -> None:
    """Write ``updates`` back to the .env in place.

    Existing keys are updated where they sit (preserving any trailing inline
    comment and the rest of the file's order/structure); unknown keys are
    appended at the end. The file is rewritten atomically via a temp file so a
    crash mid-write can't truncate the user's config.
    """
    p = Path(path)
    lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    remaining = dict(updates)
    out: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            out.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in remaining:
            after = line.split("=", 1)[1]
            idx = _inline_comment_index(after)
            comment = ("  " + after[idx:].strip()) if idx is not None else ""
            out.append(f"{key}={remaining.pop(key)}{comment}")
        else:
            out.append(line)

    for key, val in remaining.items():
        out.append(f"{key}={val}")

    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    tmp.replace(p)
