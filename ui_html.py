# -*- coding: utf-8 -*-
from __future__ import annotations

from html import escape
from textwrap import dedent
import streamlit.components.v1 as components


def safe(value) -> str:
    return escape("" if value is None else str(value))


def fmt(value, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return "--" if value is None else safe(value)


def html_block(html: str, height: int, scrolling: bool = False) -> None:
    # components.html renders real HTML in an iframe, avoiding Streamlit Markdown code-block mistakes.
    components.html(dedent(html).strip(), height=height, scrolling=scrolling)
