# -*- coding: utf-8 -*-
from __future__ import annotations

from urllib.parse import urlparse


def _safe_link(value: object) -> str:
    """Return only http(s) links; avoid passing arbitrary schemes to Markdown."""
    text = str(value or "").strip()
    try:
        parsed = urlparse(text)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            return text.replace("(", "%28").replace(")", "%29")
    except Exception:
        pass
    return ""


def _md_text(value: object) -> str:
    """Escape the small Markdown surface used by the news list."""
    text = str(value or "").replace("\n", " ").strip()
    for ch in ("\\", "`", "*", "_", "[", "]", "<", ">"):
        text = text.replace(ch, "\\" + ch)
    return text


def render_deep_report(st, forecast):
    """Render the collapsed deep report without pandas/Arrow serialization.

    The old implementation created a pandas DataFrame and called st.dataframe
    even while the expander was collapsed.  Streamlit therefore serialized the
    table through PyArrow on every forecast render.  RC25.1 keeps the same
    information but uses native text/Markdown only, removing that native crash
    surface from the main render path.
    """
    title = f"開啟完整量子分析｜{forecast.ticker.resolved_symbol}"
    with st.expander(title, expanded=False):
        st.text(str(forecast.deep_report or ""))
        news = list(forecast.news_items or [])
        st.markdown(f"**新聞來源｜{forecast.ticker.resolved_symbol}｜{len(news)}則**")
        if news:
            for idx, item in enumerate(news[:20], start=1):
                source = _md_text(getattr(item, "source", ""))
                time_label = _md_text(getattr(item, "time", ""))
                tag = _md_text(getattr(item, "tag", ""))
                title_text = _md_text(getattr(item, "title", ""))
                link = _safe_link(getattr(item, "link", ""))
                meta = "｜".join(x for x in (source, time_label, tag) if x)
                if link:
                    st.markdown(f"{idx}. [{title_text}]({link})  \n   {meta}")
                else:
                    st.markdown(f"{idx}. {title_text}  \n   {meta}")
            if len(news) > 20:
                st.caption(f"其餘 {len(news) - 20} 則未展開，避免前台一次渲染過量內容。")
            st.caption("來源：GoogleNewsTW / GoogleNewsUS｜V12 保留 V9 新聞、支撐共振與外部事件觀察；不使用 DataFrame/Arrow 渲染。")
        else:
            st.caption("本次沒有抓到可用外部新聞，或新聞資料源暫時未回傳。")
        st.markdown(f"↑ 回到 {forecast.ticker.resolved_symbol} 卡片上方")
