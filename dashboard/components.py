"""Small presentational helpers for the Streamlit dashboard (no data logic).

All text passed in is escaped here; callers never build raw HTML from data.
"""

from __future__ import annotations

import html
from typing import Iterable

import streamlit as st

from guard_pdc.theme import ALERT_COLORS, BORDER, INK, INK_MUTED, INK_SECONDARY, PAGE, PRIMARY, PRIMARY_LIGHT, SURFACE

CSS = f"""
<style>
:root {{ --pdc-ink:{INK}; --pdc-ink2:{INK_SECONDARY}; --pdc-muted:{INK_MUTED}; --pdc-border:{BORDER}; --pdc-primary:{PRIMARY}; }}
.stApp {{ background:{PAGE}; }}
.block-container {{ padding:1.1rem 2rem 3rem 2rem; max-width:1680px; }}
/* Wider sidebar drawn as a floating rounded card. */
section[data-testid="stSidebar"] {{ width:384px !important; min-width:384px !important; background:transparent; border-right:none; }}
section[data-testid="stSidebar"] > div:first-child {{
  background:{SURFACE}; margin:12px 0 12px 12px; height:calc(100vh - 24px); border-radius:18px;
  border:1px solid {BORDER}; box-shadow:0 1px 2px rgba(16,24,40,0.05), 0 4px 16px rgba(16,24,40,0.05); overflow:hidden auto;
}}
section[data-testid="stSidebar"] .block-container {{ padding-top:1rem; }}
div[data-testid="stSidebarUserContent"] {{ padding:0.4rem 1.25rem 1.5rem 1.25rem; }}
div[data-testid="stSidebarHeader"] {{ height:auto; min-height:0; padding:0.5rem 0.75rem 0 0.75rem; margin-bottom:0; }}
div[data-testid="stVerticalBlockBorderWrapper"]:has(> div > div > .pdc-card-marker) {{ background:{SURFACE}; }}
/* Header band */
.pdc-hero {{ background:linear-gradient(120deg, #0B7A80 0%, #0D5F6E 60%, #12485E 100%); color:#fff; border-radius:18px;
  padding:18px 24px; margin:0 0 12px 0; display:flex; justify-content:space-between; align-items:center; gap:18px; flex-wrap:wrap;
  box-shadow:0 6px 20px rgba(11,122,128,0.18); }}
.pdc-hero h1 {{ font-size:1.65rem; font-weight:750; margin:0; padding:0; color:#fff; letter-spacing:-0.01em; }}
.pdc-hero p {{ margin:4px 0 0 0; color:rgba(255,255,255,0.86); font-size:0.9rem; max-width:760px; }}
.pdc-hero .chips {{ display:flex; gap:6px; flex-wrap:wrap; }}
.pdc-hero .chips span {{ background:rgba(255,255,255,0.14); border:1px solid rgba(255,255,255,0.28); color:#fff; border-radius:999px;
  padding:3px 11px; font-size:0.78rem; font-weight:600; white-space:nowrap; }}
/* Sidebar brand and steps */
.pdc-brand {{ display:flex; gap:10px; align-items:center; margin:2px 0 14px 0; }}
.pdc-brand .logo {{ width:38px; height:38px; border-radius:12px; background:linear-gradient(135deg,#0B7A80,#12485E); color:#fff;
  display:flex; align-items:center; justify-content:center; font-size:20px; flex:none; }}
.pdc-brand b {{ display:block; color:{INK}; font-size:1.02rem; }}
.pdc-brand span {{ color:{INK_MUTED}; font-size:0.78rem; }}
.pdc-step {{ display:flex; align-items:center; gap:8px; margin:14px 0 4px 0; color:{INK}; font-weight:700; font-size:0.86rem;
  text-transform:uppercase; letter-spacing:0.05em; }}
.pdc-step span {{ width:22px; height:22px; border-radius:50%; background:{PRIMARY_LIGHT}; color:#075459; display:inline-flex;
  align-items:center; justify-content:center; font-size:0.75rem; }}
/* Toolbar card and pill tabs */
.st-key-toolbar {{ background:{SURFACE}; border:1px solid {BORDER}; border-radius:16px; padding:10px 16px 6px 16px; margin-bottom:4px; }}
div[data-testid="stTabs"] [role="tablist"] {{ background:{SURFACE}; border:1px solid {BORDER}; border-radius:14px; padding:5px; gap:4px; flex-wrap:wrap; }}
div[data-testid="stTabs"] [role="tablist"]::after {{ display:none; }}
div[data-testid="stTabs"] [data-testid="stTab"] {{ border-radius:10px; padding:6px 14px; }}
div[data-testid="stTabs"] [data-testid="stTab"]:hover {{ background:#F1F3F6; }}
div[data-testid="stTabs"] [data-testid="stTab"][aria-selected="true"] {{ background:{PRIMARY_LIGHT}; }}
div[data-testid="stTabs"] [data-testid="stTab"][aria-selected="true"] p {{ color:#075459; }}
div[data-testid="stTabs"] .react-aria-SelectionIndicator {{ display:none; }}
.pdc-title {{ font-size:1.55rem; font-weight:700; color:{INK}; margin:0; letter-spacing:-0.01em; }}
.pdc-subtitle {{ color:{INK_SECONDARY}; font-size:0.92rem; margin:2px 0 0 0; }}
.pdc-section {{ margin:6px 0 2px 0; }}
.pdc-section h3 {{ font-size:1.02rem; font-weight:650; color:{INK}; margin:0; padding:0; }}
.pdc-section p {{ color:{INK_SECONDARY}; font-size:0.84rem; margin:2px 0 0 0; }}
.pdc-kpis {{ display:grid; grid-template-columns:repeat(4, minmax(0, 1fr)); gap:12px; margin:6px 0 10px 0; }}
@media (max-width: 1100px) {{ .pdc-kpis {{ grid-template-columns:repeat(2, minmax(0, 1fr)); }} }}
.pdc-kpi {{ background:{SURFACE}; border:1px solid {BORDER}; border-radius:14px; padding:12px 14px; }}
.pdc-kpi .label {{ color:{INK_SECONDARY}; font-size:0.78rem; font-weight:600; text-transform:uppercase; letter-spacing:0.03em; }}
.pdc-kpi .value {{ color:{INK}; font-size:1.55rem; font-weight:700; line-height:1.25; margin-top:2px; }}
.pdc-kpi .note {{ color:{INK_MUTED}; font-size:0.78rem; margin-top:2px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
.pdc-banner {{ border-radius:14px; padding:10px 14px; margin:4px 0 10px 0; font-size:0.88rem; border:1px solid; }}
.pdc-banner b {{ font-weight:650; }}
.pdc-banner.ok {{ background:#EEF7F6; border-color:#BFE0DE; color:#0B4F53; }}
.pdc-banner.warn {{ background:#FFF6E5; border-color:#F2D49B; color:#6B4A00; }}
.pdc-banner.info {{ background:#F1F3F6; border-color:{BORDER}; color:{INK_SECONDARY}; }}
.pdc-chip {{ display:inline-block; border-radius:999px; padding:1px 9px; font-size:0.76rem; font-weight:600; margin-right:4px; border:1px solid transparent; }}
.pdc-event {{ background:{SURFACE}; border:1px solid {BORDER}; border-radius:14px; padding:14px 16px; margin-bottom:8px; }}
.pdc-event .t {{ font-size:1.08rem; font-weight:700; color:{INK}; margin:0 0 4px 0; }}
.pdc-event .m {{ color:{INK_SECONDARY}; font-size:0.85rem; margin:2px 0; }}
.pdc-event .n {{ color:{INK_MUTED}; font-size:0.8rem; margin-top:6px; }}
.pdc-swatch {{ display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:6px; vertical-align:baseline; }}
.pdc-note {{ color:{INK_MUTED}; font-size:0.8rem; margin:2px 0 8px 0; }}
.pdc-empty {{ text-align:center; color:{INK_SECONDARY}; padding:48px 16px; border:1px dashed {BORDER}; border-radius:16px; background:{SURFACE}; }}
.pdc-empty h3 {{ color:{INK}; font-size:1.1rem; margin-bottom:6px; }}
div[data-testid="stTabs"] button p {{ font-size:0.92rem; font-weight:600; }}
div[data-testid="stPlotlyChart"], div[data-testid="stDataFrame"] {{ background:{SURFACE}; border:1px solid {BORDER}; border-radius:14px; overflow:hidden; }}
div[data-testid="stStatusWidget"], details {{ border-radius:14px !important; }}
.stButton button, .stDownloadButton button, .stLinkButton a {{ border-radius:999px !important; }}
div[data-testid="stMetric"] {{ background:{SURFACE}; border:1px solid {BORDER}; border-radius:14px; padding:10px 12px; }}
</style>
"""


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def inject_css() -> None:
    st.html(CSS)


def page_header(title: str, subtitle: str, chips: Iterable[str] = ()) -> None:
    tags = "".join(f"<span>{_e(text)}</span>" for text in chips)
    st.markdown(
        f"<div class='pdc-hero'><div><h1>{_e(title)}</h1><p>{_e(subtitle)}</p></div><div class='chips'>{tags}</div></div>",
        unsafe_allow_html=True,
    )


def sidebar_brand(title: str, subtitle: str) -> None:
    st.markdown(f"<div class='pdc-brand'><div class='logo'>&#9678;</div><div><b>{_e(title)}</b><span>{_e(subtitle)}</span></div></div>", unsafe_allow_html=True)


def step(number: int, title: str) -> None:
    st.markdown(f"<div class='pdc-step'><span>{number}</span>{_e(title)}</div>", unsafe_allow_html=True)


def section(title: str, question: str | None = None) -> None:
    body = f"<p>{_e(question)}</p>" if question else ""
    st.markdown(f"<div class='pdc-section'><h3>{_e(title)}</h3>{body}</div>", unsafe_allow_html=True)


def note(text: str) -> None:
    st.markdown(f"<p class='pdc-note'>{_e(text)}</p>", unsafe_allow_html=True)


def kpis(items: Iterable[tuple[str, str, str]]) -> None:
    cards = "".join(
        f"<div class='pdc-kpi'><div class='label'>{_e(label)}</div><div class='value'>{_e(value)}</div><div class='note' title='{_e(detail)}'>{_e(detail)}</div></div>"
        for label, value, detail in items
    )
    st.markdown(f"<div class='pdc-kpis'>{cards}</div>", unsafe_allow_html=True)


def banner(kind: str, title: str, body: str = "") -> None:
    st.markdown(f"<div class='pdc-banner {_e(kind)}'><b>{_e(title)}</b>{' · ' + _e(body) if body else ''}</div>", unsafe_allow_html=True)


def alert_chip(level: str | None) -> str:
    if not level:
        return "<span class='pdc-chip' style='background:#EEF0F3;color:#4A5565'>No PDC alert level</span>"
    color = ALERT_COLORS.get(str(level).upper(), "#98A3B3")
    text = "#1B2533" if str(level).upper() in {"ADVISORY", "INFORMATION"} else "#FFFFFF"
    return f"<span class='pdc-chip' style='background:{color};color:{text}'>{_e(str(level).title())}</span>"


def chip(text: str, *, background: str = PRIMARY_LIGHT, color: str = "#075459") -> str:
    return f"<span class='pdc-chip' style='background:{background};color:{color}'>{_e(text)}</span>"


def event_card(title: str, lines: Iterable[str], chips_html: str = "", footnote: str = "", swatch: str | None = None) -> None:
    dot = f"<span class='pdc-swatch' style='background:{swatch}'></span>" if swatch else ""
    body = "".join(f"<p class='m'>{_e(line)}</p>" for line in lines if line)
    foot = f"<p class='n'>{_e(footnote)}</p>" if footnote else ""
    st.markdown(f"<div class='pdc-event'><p class='t'>{dot}{_e(title)}</p>{chips_html}{body}{foot}</div>", unsafe_allow_html=True)


def empty_state(title: str, body: str) -> None:
    st.markdown(f"<div class='pdc-empty'><h3>{_e(title)}</h3><p>{_e(body)}</p></div>", unsafe_allow_html=True)
