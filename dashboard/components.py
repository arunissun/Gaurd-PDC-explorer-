"""Small presentational helpers for the Streamlit dashboard (no data logic).

All text passed in is escaped here; callers never build raw HTML from data.

Interface colours live in INTERFACE_THEMES and are kept apart from
guard_pdc.theme, which drives every figure. Figures keep their own white
canvas, fonts and colours in both interface themes: the CSS below never
targets anything inside a figure, and pins the figure container to the
canvas colour and inherited text settings it had before the themes existed.
"""

from __future__ import annotations

import html
from typing import Iterable

import streamlit as st

from guard_pdc.theme import ALERT_COLORS, SURFACE

THEME_KEY = "ui-dark-mode"

# Interface tokens only (never used by figures).
INTERFACE_THEMES = {
    "light": {
        "page": "#F5F2ED", "sidebar": "#FFFEFB", "card": "#FFFEFB", "border": "#E2DDD6",
        "ink": "#2D2A28", "ink2": "#79736C", "accent": "#A45A45", "on-accent": "#FFFFFF", "select": "#F2E3DB",
        "hover": "#F3EFE9", "track": "#E2DDD6", "shadow": "0 1px 2px rgba(45,42,40,0.04)",
        "ok": "#A45A45", "warn-bg": "#FBF1DD", "warn-border": "#EBCF97", "warn": "#7A5200",
        "error-bg": "#FAE6E2", "error-border": "#EBB9AF", "error": "#9B2C1F",
        "info-bg": "#FFFEFB", "neutral-chip": "#EFEBE5", "neutral-chip-ink": "#5F5952",
        "tab-bg": "#A45A45", "tab-ink": "#FFFFFF", "tab-line": "none",
    },
    "dark": {
        "page": "#171819", "sidebar": "#1D1E20", "card": "#252629", "border": "#38393B",
        "ink": "#F1EEE8", "ink2": "#B2ADA6", "accent": "#D8A078", "on-accent": "#1D1E20", "select": "#34312E",
        "hover": "#2E2F32", "track": "#45464A", "shadow": "none",
        "ok": "#D8A078", "warn-bg": "#332B1D", "warn-border": "#6B5631", "warn": "#E9C77F",
        "error-bg": "#3A2421", "error-border": "#74403A", "error": "#F2A79A",
        "info-bg": "#252629", "neutral-chip": "#34312E", "neutral-chip-ink": "#D8D3CC",
        "tab-bg": "#34312E", "tab-ink": "#D8A078", "tab-line": "inset 0 -2px 0 #D8A078",
    },
}
# Interface font: Inter where installed, then Streamlit's bundled Source Sans
# (the figure and table font), then system fonts. No web font is loaded. Applied
# by CSS outside figures only: Streamlit writes its own theme font into Plotly
# text, which is why config.toml keeps font = "sans-serif".
INTERFACE_FONT = 'Inter, "Source Sans", "Segoe UI", system-ui, -apple-system, sans-serif'
# Text settings figures inherited before the interface themes; pinned on figure
# containers so interface typography never reaches them.
FIGURE_INHERITED = 'font-family:"Source Sans", sans-serif; color:#1B2533; font-size:16px; line-height:1.6; font-weight:400; letter-spacing:normal;'

_ICON_PATHS = {
    "logo": "<path d='M5 20v-7M10 20V8M15 20v-4M20 20V4'/>",
    "location": "<path d='M12 21s-6.5-5.9-6.5-11.2a6.5 6.5 0 0 1 13 0C18.5 15.1 12 21 12 21z'/><circle cx='12' cy='9.8' r='2.4'/>",
    "calendar": "<rect x='3.5' y='5' width='17' height='15.5' rx='2.2'/><path d='M3.5 10h17M8 3v4M16 3v4'/>",
    "layers": "<path d='M12 3.5 3 8.2l9 4.7 9-4.7-9-4.7z'/><path d='m3 12.4 9 4.7 9-4.7'/><path d='m3 16.6 9 4.7 9-4.7'/>",
    "people": "<circle cx='9' cy='8' r='3.2'/><path d='M3.5 19.5c.6-3.2 2.8-5 5.5-5s4.9 1.8 5.5 5'/><circle cx='17' cy='9' r='2.4'/><path d='M16 14.6c2.3.2 4 1.8 4.5 4.4'/>",
    "bars": "<path d='M5 20v-5M10 20v-9M15 20V7M20 20V4'/>",
    "warning": "<path d='M12 3.8 2.8 19.6h18.4L12 3.8z'/><path d='M12 10v4.4M12 17.1v.2'/>",
}


def icon(name: str) -> str:
    """Small stroke icon (decorative) drawn in the current text colour."""

    paths = _ICON_PATHS.get(name)
    if not paths:
        return ""
    return (
        "<svg class='pdc-icon' viewBox='0 0 24 24' width='20' height='20' fill='none' stroke='currentColor' stroke-width='1.8' "
        f"stroke-linecap='round' stroke-linejoin='round' aria-hidden='true' focusable='false'>{paths}</svg>"
    )


def _variables(tokens: dict[str, str]) -> str:
    return " ".join(f"--ui-{name}:{value};" for name, value in tokens.items())


def _css(mode: str) -> str:
    tokens = INTERFACE_THEMES[mode]
    return f"""
<style>
:root {{ {_variables(tokens)} --ui-font:{INTERFACE_FONT}; color-scheme:{mode}; }}
html, body, .stApp, div[data-testid="stAppViewContainer"] {{ background:var(--ui-page); color:var(--ui-ink); }}
.stApp {{ font-family:var(--ui-font); }}
.stApp :is(button, input, textarea, label, p, li, summary, h1, h2, h3, h4, h5, h6):not(div[data-testid="stPlotlyChart"] *) {{ font-family:var(--ui-font); }}
header[data-testid="stHeader"] {{ background:transparent; }}
.block-container {{ padding:1.1rem 2rem 3rem 2rem; max-width:1680px; }}

/* Figures: original white canvas and inherited text in both themes. */
div[data-testid="stPlotlyChart"] {{ background:{SURFACE}; border:1px solid var(--ui-border); border-radius:12px; overflow:hidden; {FIGURE_INHERITED} }}

/* Text */
.stApp [data-testid="stMarkdownContainer"], .stApp [data-testid="stWidgetLabel"], .stApp [data-testid="stWidgetLabel"] p,
.stApp [data-testid="stHeadingWithActionElements"], .stApp [data-testid="stText"], .stApp [data-testid="stSpinner"] {{ color:var(--ui-ink); font-family:var(--ui-font); }}
.stApp [data-testid="stCaptionContainer"], .stApp [data-testid="stCaptionContainer"] p {{ color:var(--ui-ink2); font-family:var(--ui-font); }}
.stApp [data-testid="stTooltipIcon"], .stApp [data-testid="stTooltipHoverTarget"] button {{ color:var(--ui-ink2); }}
.stApp [data-testid="stTooltipHoverTarget"] svg {{ color:var(--ui-ink2); stroke:var(--ui-ink2) !important; }}
[data-testid="stTooltipContent"] {{ background:var(--ui-card); color:var(--ui-ink); border:1px solid var(--ui-border); border-radius:8px; }}
[data-testid="stTooltipContent"] p {{ color:var(--ui-ink); }}
.stApp [data-testid="stIconMaterial"] {{ color:var(--ui-ink2); }}
.stApp [data-testid="stSliderThumbValue"], .stApp [data-testid="stSliderThumbValue"] p {{ color:var(--ui-accent); }}
.stApp [data-testid="stSliderTickBar"], .stApp [data-testid="stSliderTickBar"] p {{ color:var(--ui-ink2); }}
.stApp hr {{ border-color:var(--ui-border); background:var(--ui-border); }}
.stApp button [data-testid="stMarkdownContainer"], .stApp button [data-testid="stMarkdownContainer"] p {{ color:inherit; }}

/* Sidebar: compact floating card */
section[data-testid="stSidebar"] {{ width:384px !important; min-width:384px !important; background:transparent; border-right:none; }}
section[data-testid="stSidebar"] > div:first-child {{
  background:var(--ui-sidebar); margin:12px 0 12px 12px; height:calc(100vh - 24px); border-radius:12px;
  border:1px solid var(--ui-border); box-shadow:var(--ui-shadow); overflow:hidden auto;
}}
section[data-testid="stSidebar"] .block-container {{ padding-top:1rem; }}
div[data-testid="stSidebarUserContent"] {{ padding:0.4rem 1.25rem 1.5rem 1.25rem; }}
div[data-testid="stSidebarHeader"] {{ height:auto; min-height:0; padding:0.5rem 0.75rem 0 0.75rem; margin-bottom:0; }}
div[data-testid="stSidebarCollapseButton"] button, div[data-testid="stSidebarHeader"] button {{ color:var(--ui-ink2); }}
section[data-testid="stSidebar"] div[data-testid="stVerticalBlock"] {{ gap:0.7rem; }}
.pdc-brand {{ display:flex; gap:10px; align-items:center; margin:2px 0 12px 0; }}
.pdc-brand .logo {{ width:36px; height:36px; border-radius:10px; background:var(--ui-accent); color:var(--ui-on-accent);
  display:flex; align-items:center; justify-content:center; flex:none; }}
.pdc-brand b {{ display:block; color:var(--ui-ink); font-size:1rem; font-weight:650; }}
.pdc-brand span {{ color:var(--ui-ink2); font-size:0.78rem; }}
.pdc-step {{ display:flex; align-items:center; gap:8px; margin:6px 0 2px 0; padding-top:14px; border-top:1px solid var(--ui-border);
  color:var(--ui-ink); font-weight:650; font-size:0.95rem; }}
.pdc-step .pdc-icon {{ color:var(--ui-accent); width:19px; height:19px; flex:none; }}

/* Header: plain title, discreet badges, theme switch */
.st-key-pdc-header {{ margin:2px 0 4px 0; }}
/* Badges and the Dark mode switch share one right-aligned row, separated by a
   clear gap and a fine divider. */
.st-key-pdc-header [data-testid="stColumn"]:last-child > [data-testid="stVerticalBlock"] {{
  flex-direction:row; flex-wrap:wrap; justify-content:flex-end; align-items:center; gap:10px 0; padding-top:6px; }}
.st-key-pdc-header [data-testid="stColumn"]:last-child [data-testid="stElementContainer"] {{ width:auto !important; flex:0 0 auto; }}
.pdc-head h1 {{ font-size:1.75rem; font-weight:700; margin:0; padding:0; color:var(--ui-ink); letter-spacing:-0.015em; line-height:1.2; font-family:var(--ui-font); }}
.pdc-head p {{ margin:6px 0 0 0; color:var(--ui-ink2); font-size:0.9rem; max-width:780px; line-height:1.45; }}
.pdc-badges {{ display:flex; gap:6px; flex-wrap:wrap; justify-content:flex-end; }}
.pdc-badges span {{ border:1px solid var(--ui-border); background:var(--ui-card); color:var(--ui-ink); border-radius:8px;
  padding:3px 10px; font-size:0.76rem; font-weight:600; white-space:nowrap; }}
.st-key-{THEME_KEY} {{ margin-left:16px; padding-left:16px; border-left:1px solid var(--ui-border); min-height:26px; display:flex; align-items:center; }}
.st-key-{THEME_KEY} label p {{ font-size:0.85rem; font-weight:600; white-space:nowrap; }}

/* Cards */
div[data-testid="stVerticalBlockBorderWrapper"]:has(> div > div > .pdc-card-marker) {{ background:var(--ui-card); }}
.st-key-toolbar {{ background:var(--ui-card); border:1px solid var(--ui-border); border-radius:12px; padding:10px 16px 6px 16px; margin-bottom:4px; box-shadow:var(--ui-shadow); }}
.pdc-section {{ margin:6px 0 2px 0; }}
.pdc-section h3 {{ font-size:1.02rem; font-weight:650; color:var(--ui-ink); margin:0; padding:0; font-family:var(--ui-font); }}
.pdc-section p {{ color:var(--ui-ink2); font-size:0.84rem; margin:2px 0 0 0; }}
.pdc-kpis {{ display:grid; grid-template-columns:repeat(4, minmax(0, 1fr)); gap:12px; margin:6px 0 10px 0; }}
@media (max-width: 1100px) {{ .pdc-kpis {{ grid-template-columns:repeat(2, minmax(0, 1fr)); }} }}
.pdc-kpi {{ background:var(--ui-card); border:1px solid var(--ui-border); border-radius:12px; padding:12px 14px; display:flex; gap:12px; align-items:flex-start;
  box-shadow:var(--ui-shadow); min-width:0; }}
.pdc-kpi .pdc-icon {{ color:var(--ui-accent); flex:none; margin-top:1px; }}
.pdc-kpi .body {{ min-width:0; flex:1; }}
.pdc-kpi .label {{ color:var(--ui-ink2); font-size:0.74rem; font-weight:650; text-transform:uppercase; letter-spacing:0.04em; }}
.pdc-kpi .value {{ color:var(--ui-ink); font-size:1.6rem; font-weight:700; line-height:1.25; margin-top:2px; }}
.pdc-kpi .note {{ color:var(--ui-ink2); font-size:0.78rem; line-height:1.35; margin-top:3px; overflow:hidden; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; }}
.pdc-banner {{ border-radius:10px; padding:9px 14px; margin:4px 0 10px 0; font-size:0.88rem; border:1px solid var(--ui-border);
  background:var(--ui-card); color:var(--ui-ink2); }}
.pdc-banner b {{ font-weight:650; color:var(--ui-ink); }}
.pdc-banner.ok b {{ color:var(--ui-ok); }}
.pdc-banner.warn {{ background:var(--ui-warn-bg); border-color:var(--ui-warn-border); color:var(--ui-warn); }}
.pdc-banner.warn b {{ color:var(--ui-warn); }}
.pdc-banner.info {{ background:var(--ui-info-bg); }}
.pdc-chip {{ display:inline-block; border-radius:999px; padding:1px 9px; font-size:0.76rem; font-weight:600; margin-right:4px; border:1px solid transparent; }}
.pdc-chip.neutral {{ background:var(--ui-neutral-chip); color:var(--ui-neutral-chip-ink); }}
.pdc-chip.accent {{ background:var(--ui-select); color:var(--ui-accent); }}
.pdc-chip.caution {{ background:var(--ui-warn-bg); color:var(--ui-warn); }}
.pdc-event {{ background:var(--ui-card); border:1px solid var(--ui-border); border-radius:12px; padding:14px 16px; margin-bottom:8px; }}
.pdc-event .t {{ font-size:1.08rem; font-weight:700; color:var(--ui-ink); margin:0 0 4px 0; }}
.pdc-event .m {{ color:var(--ui-ink2); font-size:0.85rem; margin:2px 0; }}
.pdc-event .n {{ color:var(--ui-ink2); font-size:0.8rem; margin-top:6px; opacity:0.9; }}
.pdc-swatch {{ display:inline-block; width:10px; height:10px; border-radius:50%; margin-right:6px; vertical-align:baseline; }}
p.pdc-note {{ color:var(--ui-ink2); font-size:0.82rem; line-height:1.45; margin:2px 0 8px 0; }}
.pdc-sizes {{ display:flex; align-items:flex-end; gap:18px; flex-wrap:wrap; margin:6px 0 6px 4px; }}
.pdc-sizes .h {{ color:var(--ui-ink2); font-size:0.8rem; font-weight:600; align-self:center; margin-right:2px; }}
.pdc-sizes .i {{ display:flex; flex-direction:column; align-items:center; gap:3px; color:var(--ui-ink2); font-size:0.76rem; }}
.pdc-sizes .c {{ border-radius:50%; background:rgba(128,122,115,0.18); border:1.5px solid var(--ui-ink2); box-sizing:border-box; }}
.pdc-empty {{ text-align:center; color:var(--ui-ink2); padding:48px 16px; border:1px dashed var(--ui-border); border-radius:12px; background:var(--ui-card); }}
.pdc-empty h3 {{ color:var(--ui-ink); font-size:1.1rem; margin-bottom:6px; font-family:var(--ui-font); }}

/* Navigation */
div[data-testid="stTabs"] [role="tablist"] {{ background:var(--ui-card); border:1px solid var(--ui-border); border-radius:12px; padding:5px; gap:4px; flex-wrap:wrap; box-shadow:var(--ui-shadow); }}
div[data-testid="stTabs"] [role="tablist"]::after {{ display:none; }}
div[data-testid="stTabs"] [data-testid="stTab"] {{ border-radius:8px; padding:6px 14px; color:var(--ui-ink); }}
div[data-testid="stTabs"] [data-testid="stTab"] p {{ font-size:0.92rem; font-weight:600; color:var(--ui-ink); }}
div[data-testid="stTabs"] [data-testid="stTab"]:hover {{ background:var(--ui-hover); }}
div[data-testid="stTabs"] [data-testid="stTab"][aria-selected="true"] {{ background:var(--ui-tab-bg); box-shadow:var(--ui-tab-line); }}
div[data-testid="stTabs"] [data-testid="stTab"][aria-selected="true"] p {{ color:var(--ui-tab-ink); }}
div[data-testid="stTabs"] .react-aria-SelectionIndicator {{ display:none; }}

/* Buttons */
.stApp [data-testid="stBaseButton-secondary"], .stApp [data-testid^="stBaseLinkButton"] {{ background:var(--ui-card); color:var(--ui-ink); border:1px solid var(--ui-border); border-radius:10px !important; }}
.stApp [data-testid="stBaseButton-secondary"]:hover, .stApp [data-testid^="stBaseLinkButton"]:hover {{ border-color:var(--ui-accent); color:var(--ui-accent); background:var(--ui-card); }}
.stApp [data-testid="stBaseButton-primary"] {{ background:var(--ui-accent); color:var(--ui-on-accent); border:1px solid var(--ui-accent); border-radius:10px !important; font-weight:600; }}
.stApp [data-testid="stBaseButton-primary"]:hover {{ background:var(--ui-accent); color:var(--ui-on-accent); border-color:var(--ui-accent); filter:brightness(1.07); }}
.stApp [data-testid="stBaseButton-primary"]:disabled {{ opacity:0.45; filter:none; }}

/* Pills and segmented controls: neutral selection, fine borders */
.stApp button[data-variant="pills"], .stApp button[data-variant="segmented_control"] {{ background:var(--ui-card); color:var(--ui-ink); border-color:var(--ui-border); }}
.stApp button[data-variant="pills"]:hover, .stApp button[data-variant="segmented_control"]:hover {{ color:var(--ui-accent); border-color:var(--ui-accent); }}
.stApp button[data-variant="pills"][data-selected="true"], .stApp button[data-variant="segmented_control"][data-selected="true"] {{
  background:var(--ui-select); color:var(--ui-accent); border-color:var(--ui-accent); }}
.stApp button[data-variant="pills"] p, .stApp button[data-variant="segmented_control"] p {{ font-size:0.85rem; }}

/* Inputs, selects and their menus */
.stApp [data-testid="stMultiSelect"] [role="group"], .stApp [data-testid="stSelectbox"] [role="group"],
.stApp [data-testid="stSelectbox"] [data-rac] > button {{ background:var(--ui-card); border-color:var(--ui-border); color:var(--ui-ink); }}
.stApp [data-testid="stMultiSelect"] input, .stApp [data-testid="stSelectbox"] input {{ color:var(--ui-ink); }}
.stApp [data-testid="stMultiSelect"] button, .stApp [data-testid="stSelectbox"] button {{ color:var(--ui-ink2); }}
.stApp [data-testid="stMultiSelect"] [data-tag] {{ background:var(--ui-accent); color:var(--ui-on-accent); }}
.stApp [data-testid="stMultiSelect"] [data-tag] button {{ color:var(--ui-on-accent); }}
[data-testid$="Dropdown"], [data-testid$="Dropdown"] [role="listbox"] {{ background:var(--ui-card); color:var(--ui-ink); border-color:var(--ui-border); }}
[data-testid$="Dropdown"] [role="option"] {{ color:var(--ui-ink); }}
[data-testid$="Dropdown"] [role="option"][data-focused="true"], [data-testid$="Dropdown"] [role="option"]:hover {{ background:var(--ui-select); }}

/* Status, notices, expanders, tables, JSON, progress */
.stApp [data-testid="stExpander"] details {{ background:var(--ui-card); border:1px solid var(--ui-border); border-radius:10px !important; }}
.stApp [data-testid="stExpander"] summary, .stApp [data-testid="stExpander"] summary p {{ color:var(--ui-ink); }}
.stApp [data-testid="stExpander"] summary:hover, .stApp [data-testid="stExpander"] summary:hover p {{ color:var(--ui-accent); }}
.stApp [data-testid="stExpanderIconCheck"] {{ color:var(--ui-ok); }}
.stApp [data-testid="stAlertContainer"] {{ border-radius:10px; border:1px solid var(--ui-border); }}
.stApp [data-testid="stAlertContentInfo"], .stApp [data-testid="stAlertContentSuccess"] {{ color:var(--ui-ink); }}
.stApp [data-testid="stAlertContainer"]:has([data-testid="stAlertContentInfo"]),
.stApp [data-testid="stAlertContainer"]:has([data-testid="stAlertContentSuccess"]) {{ background:var(--ui-info-bg); }}
.stApp [data-testid="stAlertContainer"]:has([data-testid="stAlertContentWarning"]) {{ background:var(--ui-warn-bg); border-color:var(--ui-warn-border); }}
.stApp [data-testid="stAlertContentWarning"], .stApp [data-testid="stAlertContentWarning"] p {{ color:var(--ui-warn); }}
.stApp [data-testid="stAlertContainer"]:has([data-testid="stAlertContentError"]) {{ background:var(--ui-error-bg); border-color:var(--ui-error-border); }}
.stApp [data-testid="stAlertContentError"], .stApp [data-testid="stAlertContentError"] p {{ color:var(--ui-error); }}
.stApp [data-testid="stAlertContentInfo"] p, .stApp [data-testid="stAlertContentSuccess"] p {{ color:var(--ui-ink); }}
div[data-testid="stDataFrame"] {{ border:1px solid var(--ui-border); border-radius:12px; overflow:hidden; }}
.stApp [data-testid="stJson"] {{ background:var(--ui-card); border:1px solid var(--ui-border); border-radius:10px; padding:6px 8px; }}
.stApp [data-testid="stJson"] .react-json-view {{ background-color:transparent !important; }}
.stApp [data-testid="stProgress"] p {{ color:var(--ui-ink2); }}
div[data-testid="stStatusWidget"] {{ border-radius:10px !important; }}
{_DARK_EXTRA if mode == "dark" else ""}
</style>
"""


# Native widgets that Streamlit colours from its single (light) base theme and
# that need explicit dark values. Filled in from the rendered page.
_DARK_EXTRA = """
/* Slider fill: its gradient stops follow the value, so it is recoloured
   (terracotta -> copper-peach) with a filter instead of new stops. */
.stApp [data-testid="stSlider"] [role="group"] > div > div:first-child { filter:hue-rotate(16deg) saturate(0.6) brightness(1.62); }
.stApp [data-testid="stSlider"] [role="group"] > div > div[data-rac] { background:var(--ui-accent); }
/* Switches */
.stApp [data-testid="stCheckbox"] label:has(input[role="switch"]) > div:not([data-testid]) { background:var(--ui-track); }
.stApp [data-testid="stCheckbox"] label:has(input[role="switch"]:checked) > div:not([data-testid]) { background:var(--ui-accent); }
.stApp [data-testid="stCheckbox"] label:has(input[role="switch"]) > div:not([data-testid]) > div { background:var(--ui-ink); }
.stApp [data-testid="stProgress"] [role="progressbar"] { background:var(--ui-track); }
/* JSON viewer: its light palette is inline, so dark values need !important. */
.stApp [data-testid="stJson"] .react-json-view * { color:var(--ui-ink) !important; }
.stApp [data-testid="stJson"] .variable-value > div:has(.string-value), .stApp [data-testid="stJson"] .string-value,
.stApp [data-testid="stJson"] .node-ellipsis { color:var(--ui-accent) !important; }
.stApp [data-testid="stJson"] .react-json-view svg, .stApp [data-testid="stJson"] .react-json-view svg * { color:var(--ui-ink2) !important; }
.stApp [data-testid="stJson"] .variable-row, .stApp [data-testid="stJson"] .pushed-content { border-left-color:var(--ui-border) !important; }
"""


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def dark_mode() -> bool:
    """The interface theme chosen in this browser session (light by default)."""

    return bool(st.session_state.get(THEME_KEY, False))


def inject_css() -> None:
    st.html(_css("dark" if dark_mode() else "light"))


def page_header(title: str, subtitle: str, chips: Iterable[str] = ()) -> None:
    """Title, subtitle, discreet source badges and the Dark mode switch."""

    tags = "".join(f"<span>{_e(text)}</span>" for text in chips)
    with st.container(key="pdc-header"):
        text, side = st.columns((6.2, 3.2), vertical_alignment="top")
        text.markdown(f"<div class='pdc-head'><h1>{_e(title)}</h1><p>{_e(subtitle)}</p></div>", unsafe_allow_html=True)
        side.markdown(f"<div class='pdc-badges'>{tags}</div>", unsafe_allow_html=True)
        # Widget state lives in the session, so the choice survives reruns and
        # tab changes; switching only reruns the page with the stored data.
        side.toggle("Dark mode", key=THEME_KEY, help="Interface colours only. Figures keep their white canvas.")


def sidebar_brand(title: str, subtitle: str) -> None:
    st.markdown(f"<div class='pdc-brand'><div class='logo'>{icon('logo')}</div><div><b>{_e(title)}</b><span>{_e(subtitle)}</span></div></div>", unsafe_allow_html=True)


def sidebar_section(icon_name: str, title: str) -> None:
    st.markdown(f"<div class='pdc-step'>{icon(icon_name)}{_e(title)}</div>", unsafe_allow_html=True)


def section(title: str, question: str | None = None) -> None:
    body = f"<p>{_e(question)}</p>" if question else ""
    st.markdown(f"<div class='pdc-section'><h3>{_e(title)}</h3>{body}</div>", unsafe_allow_html=True)


def note(text: str) -> None:
    st.markdown(f"<p class='pdc-note'>{_e(text)}</p>", unsafe_allow_html=True)


def kpis(items: Iterable[tuple[str, ...]]) -> None:
    """Metric cards from (label, value, detail) or (label, value, detail, icon) tuples."""

    cards = []
    for label, value, detail, *rest in items:
        mark = icon(rest[0]) if rest else ""
        cards.append(
            f"<div class='pdc-kpi'>{mark}<div class='body'><div class='label'>{_e(label)}</div><div class='value'>{_e(value)}</div>"
            f"<div class='note' title='{_e(detail)}'>{_e(detail)}</div></div></div>"
        )
    st.markdown(f"<div class='pdc-kpis'>{''.join(cards)}</div>", unsafe_allow_html=True)


def banner(kind: str, title: str, body: str = "") -> None:
    st.markdown(f"<div class='pdc-banner {_e(kind)}'><b>{_e(title)}</b>{' · ' + _e(body) if body else ''}</div>", unsafe_allow_html=True)


def alert_chip(level: str | None) -> str:
    if not level:
        return "<span class='pdc-chip neutral'>No PDC alert level</span>"
    color = ALERT_COLORS.get(str(level).upper(), "#98A3B3")
    text = "#1B2533" if str(level).upper() in {"ADVISORY", "INFORMATION"} else "#FFFFFF"
    return f"<span class='pdc-chip' style='background:{color};color:{text}'>{_e(str(level).title())}</span>"


def chip(text: str, *, background: str | None = None, color: str | None = None, tone: str = "accent") -> str:
    """A small label: explicit colours (for data encodings such as hazard) or an interface tone."""

    if background is not None:
        return f"<span class='pdc-chip' style='background:{background};color:{color or '#FFFFFF'}'>{_e(text)}</span>"
    return f"<span class='pdc-chip {_e(tone)}'>{_e(text)}</span>"


def event_card(title: str, lines: Iterable[str], chips_html: str = "", footnote: str = "", swatch: str | None = None) -> None:
    dot = f"<span class='pdc-swatch' style='background:{swatch}'></span>" if swatch else ""
    body = "".join(f"<p class='m'>{_e(line)}</p>" for line in lines if line)
    foot = f"<p class='n'>{_e(footnote)}</p>" if footnote else ""
    st.markdown(f"<div class='pdc-event'><p class='t'>{dot}{_e(title)}</p>{chips_html}{body}{foot}</div>", unsafe_allow_html=True)


def size_legend(title: str, items: Iterable[tuple[str, float]]) -> None:
    """Marker-size key drawn at the exact pixel diameters used on the map."""

    entries = "".join(
        f"<div class='i'><div class='c' style='width:{diameter:.1f}px;height:{diameter:.1f}px'></div>{_e(label)}</div>"
        for label, diameter in items
    )
    if entries:
        st.markdown(f"<div class='pdc-sizes'><span class='h'>{_e(title)}</span>{entries}</div>", unsafe_allow_html=True)


def empty_state(title: str, body: str) -> None:
    st.markdown(f"<div class='pdc-empty'><h3>{_e(title)}</h3><p>{_e(body)}</p></div>", unsafe_allow_html=True)
