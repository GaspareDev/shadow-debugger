"""Syntax check for code snippets suggested by the model."""

from __future__ import annotations

import ast
import re
import textwrap
import warnings
from dataclasses import asdict, dataclass
from typing import Any, Dict, List

_CODE_BLOCK = re.compile(r"```(?:python|py)[ \t]*\r?\n(.*?)```", re.DOTALL | re.IGNORECASE)
_LINE_NUMBER_PREFIX = re.compile(r"^(?:>>)?\s*\d+:\s?")


@dataclass
class PatchCheckResult:
    attempted: bool
    success: bool
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def extract_python_blocks(markdown: str) -> List[str]:
    return [m.strip("\n") for m in _CODE_BLOCK.findall(markdown or "") if m.strip()]


def _clean(snippet: str) -> str:
    lines = snippet.splitlines()
    if lines and all(_LINE_NUMBER_PREFIX.match(line) for line in lines if line.strip()):
        lines = [_LINE_NUMBER_PREFIX.sub("", line, count=1) for line in lines]
    return textwrap.dedent("\n".join(lines))


def check_patch_syntax(ai_analysis: str) -> PatchCheckResult:
    blocks = extract_python_blocks(ai_analysis)
    if not blocks:
        return PatchCheckResult(False, False, "No ```python block found in the analysis.")

    errors: List[str] = []
    for index, block in enumerate(blocks, start=1):
        source = _clean(block)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                ast.parse(source)
        except SyntaxError as exc:
            errors.append(f"block {index}, line {exc.lineno}: {exc.msg}")

    if errors:
        return PatchCheckResult(True, False, "Proposed code has syntax errors: " + "; ".join(errors))
    noun = "snippet" if len(blocks) == 1 else "snippets"
    return PatchCheckResult(
        True, True, f"{len(blocks)} proposed {noun} parsed cleanly."
    )
