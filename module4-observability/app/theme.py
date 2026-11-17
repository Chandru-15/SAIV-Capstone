from __future__ import annotations

import streamlit as st

STATUS_COLORS = {
    "approved": "#22C55E",
    "flagged": "#F59E0B",
    "rejected": "#EF4444",
    "pending": "#3B82F6",
    "appealed": "#A78BFA",
    "scheduled": "#3B82F6",
    "active": "#22C55E",
    "closed": "#94A3B8",
    "cancelled": "#EF4444",
}

PALETTES = {
    "light": {
        "surface": "#FFFFFF",
        "axis_label": "#334155",
        "axis_title": "#64748B",
        "grid": "rgba(15,23,42,0.08)",
        "domain": "rgba(15,23,42,0.16)",
        "track": "#D9DFE8",
        "bar": "#64748B",
        "heat_low": "#F1F5F9",
        "heat_mid": "#7DD3FC",
        "heat_high": "#22C55E",
        "heat_text": "#0F172A",
    },
}

OK_FILL = "#22C55E"

def active_mode() -> str:
    return "light"

def palette() -> dict:
    return PALETTES["light"]

FONT_UI = "'Schibsted Grotesk', Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"

CSS_LIGHT = """
:root {
  --bg: #EEF2F6; 
  --surface: #FFFFFF;
  --surface-2: #F8FAFC;
  --surface-3: #F1F5F9;
  --sidebar-bg: #0F172A !important; 
  --input-bg: #FFFFFF;
  --line: rgba(15, 23, 42, 0.08);
  --line-strong: rgba(15, 23, 42, 0.15);
  --line-focus: rgba(15, 23, 42, 0.4);
  --text: #0F172A;
  --muted: #526077;
  --soft: #334155;
  --faint: #94A3B8;
  --ok: #22C55E;
  --flag: #F59E0B;
  --wait: #3B82F6;
  --bad: #EF4444;
  --appeal: #A78BFA;
  --ok-t: #15803D;
  --flag-t: #B45309;
  --wait-t: #1D4ED8;
  --bad-t: #B91C1C;
  --appeal-t: #6D28D9;
  --bad-soft: #B91C1C;
  --flag-soft: #B45309;
  --ok-glow: rgba(34, 197, 94, 0.2);
  --hover: rgba(255, 255, 255, 0.1);
  --active: rgba(255, 255, 255, 0.15);
  --active-hover: rgba(255, 255, 255, 0.2);
  --track: rgba(15, 23, 42, 0.1);
  --track-strong: #CBD5E1;
  --scroll: #CBD5E1;
  --btn-bg: #0F172A !important; 
  --btn-fg: #FFFFFF !important;
  --btn-hover: #1E293B !important;
  --shadow: rgba(15, 23, 42, 0.08);
  --glow: rgba(15, 23, 42, 0.02);
  color-scheme: light;
}
"""

CSS_BASE = """
@import url('https://fonts.googleapis.com/css2?family=Schibsted+Grotesk:wght@400;500;600;700&display=swap');

#MainMenu { visibility: hidden !important; }
header { visibility: hidden !important; }
[data-testid="stHeader"] { display: none !important; }

html, body, [class*="css"], .stApp, .stMarkdown, p, label, input, textarea, button {
  font-family: 'Schibsted Grotesk', Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif !important;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

.stApp {
  background: var(--bg) !important;
  color: var(--text);
}

section[data-testid="stSidebar"],
section[data-testid="stSidebar"] > div,
div[data-testid="stSidebarContent"],
div[data-testid="stAppViewContainer"],
div[data-testid="stAppViewContainer"] > section {
  transition: none !important;
  animation: none !important;
}

div[data-testid="stHtml"],
div[data-testid="stCustomComponentV1"]:has(iframe[height="0"]) {
  display: none !important;
  height: 0 !important;
  min-height: 0 !important;
  margin: 0 !important;
  padding: 0 !important;
  overflow: hidden !important;
}

footer, .stDeployButton,
[data-testid="stAppDeployButton"],
div[data-testid="stDecoration"],
div[data-testid="stStatusWidget"] {
  visibility: hidden;
  height: 0;
  display: none !important;
}

header[data-testid="stHeader"] {
  background: transparent !important;
  height: 0 !important;
}

.block-container {
  padding-top: 1.5rem !important;
  padding-bottom: 3.5rem !important;
  max-width: 1320px;
  padding-left: 2rem !important;
  padding-right: 2rem !important;
}

section[data-testid="stSidebar"] {
  background: #0F172A !important;
  border-right: none !important;
  min-width: 280px !important;
  max-width: 280px !important;
  display: block !important;
  visibility: visible !important;
  transform: none !important;
  transition: none !important;
}

section[data-testid="stSidebar"] > div:first-child {
  padding: 1.5rem 1.05rem 1.2rem 1.05rem !important;
}

section[data-testid="stSidebar"] [data-testid="block-container"] {
  width: 100% !important;
  max-width: 100% !important;
  padding-left: 0 !important;
  padding-right: 0 !important;
}

section[data-testid="stSidebar"] [data-testid="stVerticalBlock"],
section[data-testid="stSidebar"] [data-testid="element-container"],
section[data-testid="stSidebar"] [data-testid="stElementContainer"],
section[data-testid="stSidebar"] [data-testid="stMarkdown"],
section[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] {
  width: 100% !important;
  max-width: 100% !important;
}

div[data-testid="stSidebarNav"] { display: none; }

[data-testid="collapsedControl"],
[data-testid="stSidebarCollapsedControl"],
[data-testid="stExpandSidebarButton"],
[data-testid="stCollapseSidebarButton"],
section[data-testid="stSidebar"] button[kind="header"],
section[data-testid="stSidebar"] button[kind="headerNoPadding"],
section[data-testid="stSidebar"] [data-testid="baseButton-header"],
section[data-testid="stSidebar"] [data-testid="baseButton-headerNoPadding"],
section[data-testid="stSidebar"] [data-testid="stBaseButton-header"],
section[data-testid="stSidebar"] [data-testid="stBaseButton-headerNoPadding"] {
  display: none !important;
  visibility: hidden !important;
  pointer-events: none !important;
  width: 0 !important;
  height: 0 !important;
  min-width: 0 !important;
  padding: 0 !important;
  overflow: hidden !important;
}

section[data-testid="stSidebar"] button[kind="secondary"],
section[data-testid="stSidebar"] [data-testid="stBaseButton-secondary"],
section[data-testid="stSidebar"] [data-testid="baseButton-secondary"] {
  background: transparent !important;
  color: #FFFFFF !important; 
  border: 1px solid transparent !important;
  justify-content: flex-start !important;
  text-align: left !important;
  font-weight: 500 !important;
  font-size: 0.92rem !important;
  border-radius: 8px !important;
  padding: 0.6rem 0.9rem !important;
  box-shadow: none !important;
  width: 100%;
  transition: all 0.2s ease;
}

section[data-testid="stSidebar"] button[kind="secondary"]:hover,
section[data-testid="stSidebar"] [data-testid="stBaseButton-secondary"]:hover,
section[data-testid="stSidebar"] [data-testid="baseButton-secondary"]:hover {
  background: var(--hover) !important;
  color: #FFFFFF !important;
  border-color: transparent !important;
  filter: none;
}

section[data-testid="stSidebar"] button[kind="primary"],
section[data-testid="stSidebar"] [data-testid="stBaseButton-primary"],
section[data-testid="stSidebar"] [data-testid="baseButton-primary"] {
  background: rgba(255, 255, 255, 0.1) !important;
  color: #FFFFFF !important;
  border: 1px solid rgba(255, 255, 255, 0.15) !important;
  justify-content: flex-start !important;
  text-align: left !important;
  font-weight: 600 !important;
  font-size: 0.92rem !important;
  border-radius: 8px !important;
  padding: 0.6rem 0.9rem !important;
  box-shadow: none !important;
}

section[data-testid="stSidebar"] button[kind="primary"]:hover,
section[data-testid="stSidebar"] [data-testid="stBaseButton-primary"]:hover,
section[data-testid="stSidebar"] [data-testid="baseButton-primary"]:hover {
  background: rgba(255, 255, 255, 0.15) !important;
  color: #FFFFFF !important;
  border-color: rgba(255, 255, 255, 0.2) !important;
  filter: none;
}

section[data-testid="stSidebar"] .workspace-tools {
  height: 0;
  margin: 0;
  padding: 0;
  overflow: hidden;
}

section[data-testid="stSidebar"] [data-testid="stElementContainer"]:has(.workspace-tools),
section[data-testid="stSidebar"] [data-testid="element-container"]:has(.workspace-tools),
section[data-testid="stSidebar"] [data-testid="stElementContainer"]:has(.account-tools),
section[data-testid="stSidebar"] [data-testid="element-container"]:has(.account-tools) {
  height: 0 !important;
  min-height: 0 !important;
  margin: 0 !important;
  padding: 0 !important;
  overflow: hidden !important;
}

section[data-testid="stSidebar"] [data-testid="stElementContainer"]:has(.workspace-tools) ~ [data-testid="stElementContainer"] .stButton,
section[data-testid="stSidebar"] [data-testid="element-container"]:has(.workspace-tools) ~ [data-testid="element-container"] .stButton {
  margin-bottom: 0.4rem !important;
}

section[data-testid="stSidebar"] [data-testid="stElementContainer"]:has(.workspace-tools) ~ [data-testid="stElementContainer"] button,
section[data-testid="stSidebar"] [data-testid="element-container"]:has(.workspace-tools) ~ [data-testid="element-container"] button,
section[data-testid="stSidebar"] [data-testid="stElementContainer"]:has(.workspace-tools) ~ [data-testid="stElementContainer"] [data-testid^="baseButton-"],
section[data-testid="stSidebar"] [data-testid="element-container"]:has(.workspace-tools) ~ [data-testid="element-container"] [data-testid^="baseButton-"] {
  background: #1E293B !important;
  color: #FFFFFF !important;
  border: 1px solid #334155 !important;
  justify-content: center !important;
  text-align: center !important;
  font-weight: 600 !important;
  font-size: 0.84rem !important;
  border-radius: 8px !important;
  padding: 0.55rem 0.75rem !important;
  box-shadow: none !important;
  width: 100% !important;
  min-height: 2.4rem !important;
  white-space: nowrap !important;
}

section[data-testid="stSidebar"] [data-testid="stElementContainer"]:has(.workspace-tools) ~ [data-testid="stElementContainer"] button:hover,
section[data-testid="stSidebar"] [data-testid="element-container"]:has(.workspace-tools) ~ [data-testid="element-container"] button:hover {
  background: #334155 !important;
  color: #FFFFFF !important;
  border-color: #475569 !important;
  filter: none;
}

section[data-testid="stSidebar"] hr {
  display: none !important;
}

section[data-testid="stSidebar"] [data-testid="stCheckbox"] label,
section[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p {
  font-size: 0.8rem !important;
  color: #94A3B8 !important;
  font-weight: 500 !important;
}

section[data-testid="stSidebar"] .stButton {
  margin-bottom: 0.22rem;
}

section[data-testid="stSidebar"] [data-testid="column"] button,
section[data-testid="stSidebar"] [data-testid="column"] [data-testid^="baseButton-"] {
  background: #1E293B !important;
  color: #FFFFFF !important;
  border: 1px solid #334155 !important;
  justify-content: center !important;
  text-align: center !important;
  font-weight: 600 !important;
  font-size: 0.78rem !important;
  border-radius: 8px !important;
  padding: 0.5rem 0.35rem !important;
  box-shadow: none !important;
  width: 100% !important;
  min-height: 2.35rem !important;
  white-space: nowrap !important;
}

.nav-tab-ghost {
  color: #94A3B8;
  font-size: 0.92rem;
  font-weight: 500;
  padding: 0.58rem 0.9rem;
  border-radius: 8px;
  border: 1px solid transparent;
  margin-bottom: 0.22rem;
}

.nav-label {
  font-size: 0.75rem !important;
  font-weight: 600 !important;
  color: #8A99B0 !important;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  margin: 1.6rem 0 0.75rem 0.5rem !important;
}

.saiv-mark {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0 0.15rem 0.5rem 0.15rem !important;
  margin-bottom: 0.5rem !important;
}

.saiv-hex { width: 36px; height: 36px; flex-shrink: 0; }

.saiv-brand {
  font-weight: 700;
  font-size: 1.25rem;
  letter-spacing: -0.03em;
  color: #FFFFFF !important; 
  line-height: 1.1;
}

.saiv-sub {
  font-size: 0.75rem;
  color: #94A3B8;
  margin-top: 0.12rem;
}

.user-chip {
  margin-top: 1.5rem;
  padding: 0.85rem 0.9rem;
  border-radius: 10px;
  background: #1E293B !important;
  border: 1px solid #334155 !important;
  box-shadow: none !important;
  overflow: hidden;
  width: 100%;
  box-sizing: border-box;
}

.user-chip .name {
  font-weight: 600;
  font-size: 0.88rem;
  color: #FFFFFF !important;
}

.user-chip .meta,
.user-chip .meta p,
.user-chip .meta span {
  color: #94A3B8 !important;
  font-size: 0.75rem !important;
  margin-top: 0.18rem;
  line-height: 1.35;
  white-space: nowrap !important;
  overflow: hidden !important;
  text-overflow: ellipsis !important;
  word-break: keep-all !important;
  overflow-wrap: normal !important;
}

.role-pill {
  display: inline-block;
  margin-top: 0.6rem;
  padding: 0.15rem 0.6rem;
  border-radius: 999px;
  font-size: 0.72rem;
  font-weight: 600;
  color: #94A3B8 !important;
  background: #0F172A !important;
  border: 1px solid #334155 !important;
}

.access-note {
  margin-top: 0.45rem;
  color: #64748B;
  font-size: 0.72rem;
  line-height: 1.4;
}

.health-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.35rem;
  margin: 0.2rem 0 0.4rem 0;
}

.health-pill {
  display: inline-flex;
  align-items: center;
  gap: 0.38rem;
  padding: 0.16rem 0.55rem;
  border-radius: 999px;
  font-size: 0.74rem;
  font-weight: 500;
  border: 1px solid #334155;
  color: #94A3B8;
  background: #1E293B;
}

.health-pill .dot { width: 6px; height: 6px; border-radius: 50%; background: #64748B; }
.health-pill.up { color: #FFFFFF; }
.health-pill.up .dot { background: var(--ok); }
.health-pill.down { color: #F87171; border-color: rgba(239, 68, 68, 0.35); }
.health-pill.down .dot { background: var(--bad); }

.sys-list { display: flex; flex-direction: column; gap: 0.15rem; margin: 0.15rem 0 0.35rem 0; }

.sys-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.4rem 0.5rem;
  border-radius: 6px;
  font-size: 0.82rem;
  color: #94A3B8;
}

.sys-row .sys-name { font-weight: 500; }
.sys-row .sys-state { font-size: 0.76rem; font-weight: 600; }
.sys-row.up .sys-state { color: #4ADE80; }
.sys-row.down .sys-state { color: #F87171; }

.page-title {
  font-size: 2.05rem;
  font-weight: 600;
  letter-spacing: -0.035em;
  margin: 0 0 0.3rem 0;
  line-height: 1.1;
  color: var(--text);
}

.page-lead {
  color: var(--muted);
  margin: 0 0 1rem 0;
  font-size: 0.95rem;
  line-height: 1.5;
  max-width: 44rem;
}

.clock {
  white-space: nowrap;
  color: var(--muted);
  font-size: 0.82rem;
  text-align: right;
  line-height: 1.45;
  padding-top: 0.55rem;
  font-variant-numeric: tabular-nums;
}

.live-dot {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.8rem;
  color: var(--muted);
  font-weight: 500;
  margin-bottom: 1.1rem;
}
.live-dot::before {
  content: "";
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--ok);
  box-shadow: 0 0 0 4px var(--ok-glow);
}

.sec {
  font-size: 1.05rem;
  font-weight: 600;
  letter-spacing: -0.02em;
  color: var(--text);
  margin: 1.5rem 0 0.6rem 0;
}

.panel-head { margin: 0 0 0.35rem 0; }
.panel-head h3 {
  font-size: 1rem;
  font-weight: 600;
  letter-spacing: -0.02em;
  margin: 0;
  color: var(--text);
}
.panel-head p {
  color: var(--muted);
  font-size: 0.82rem;
  margin: 0.15rem 0 0 0;
}

div[data-testid="stVerticalBlockBorderWrapper"][data-border="true"] {
  background: var(--surface);
  border: 1px solid var(--line) !important;
  border-radius: 12px !important;
  padding: 0.35rem 0.5rem 0.5rem 0.5rem;
}

div[data-testid="stVerticalBlockBorderWrapper"][data-border="true"] div[data-testid="stVerticalBlockBorderWrapper"][data-border="true"] {
  background: transparent;
  border: 0 !important;
  padding: 0;
}

.now {
  padding: 1.35rem 1.5rem 1.2rem 1.5rem;
  border: 1px solid var(--line);
  border-radius: 14px;
  background: var(--surface);
  margin-bottom: 1rem;
  box-shadow: 0 1px 3px var(--shadow);
}

.now-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 1rem;
  flex-wrap: wrap;
}

.now-title { font-size: 1.05rem; font-weight: 600; letter-spacing: -0.02em; }
.now-meta { color: var(--muted); font-size: 0.86rem; }

.now-count { margin-top: 0.7rem; display: flex; align-items: baseline; gap: 0.6rem; }
.now-count b {
  font-size: 3.4rem;
  font-weight: 500;
  letter-spacing: -0.045em;
  line-height: 1;
  font-variant-numeric: tabular-nums;
}
.now-count span { color: var(--muted); font-size: 0.95rem; }

.abar {
  display: flex;
  gap: 2px;
  height: 14px;
  margin: 1.1rem 0 1rem 0;
}

.abar .seg {
  display: block;
  min-width: 4px;
  border-radius: 4px;
}
.abar .seg.ok { background: var(--ok); }
.abar .seg.flag { background: var(--flag); }
.abar .seg.wait { background: var(--wait); }
.abar .seg.bad { background: var(--bad); }
.abar .seg.off { background: var(--track); }

.abar.mini { height: 8px; gap: 1.5px; margin: 0; }
.abar.mini .seg { min-width: 3px; border-radius: 3px; }

.sw {
  display: inline-block;
  width: 10px;
  height: 10px;
  border-radius: 3px;
}
.sw.ok { background: var(--ok); }
.sw.flag { background: var(--flag); }
.sw.wait { background: var(--wait); }
.sw.bad { background: var(--bad); }
.sw.off { background: var(--track); }

.now-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem 1.25rem;
  color: var(--muted);
  font-size: 0.8rem;
}
.now-legend span { display: inline-flex; align-items: center; gap: 0.4rem; }
.now-legend b { color: var(--text); font-weight: 600; font-variant-numeric: tabular-nums; }

.srow {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) 4.2rem minmax(0, 2fr) 4.5rem;
  gap: 1rem;
  align-items: center;
  padding: 0.65rem 0;
  border-top: 1px solid var(--line);
}
.srow:first-of-type { border-top: 0; }
.srow-name { font-weight: 500; font-size: 0.92rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.srow-name span { display: block; color: var(--muted); font-size: 0.78rem; font-weight: 400; }
.srow-time { color: var(--soft); font-size: 0.88rem; font-variant-numeric: tabular-nums; }
.srow-n { text-align: right; color: var(--soft); font-size: 0.88rem; font-variant-numeric: tabular-nums; }

.stats { display: flex; flex-wrap: wrap; gap: 0.4rem 2.6rem; margin: 0.2rem 0 1.2rem 0; }
.stat-v {
  font-size: 1.7rem;
  font-weight: 500;
  letter-spacing: -0.03em;
  font-variant-numeric: tabular-nums;
  line-height: 1.15;
}
.stat-l { color: var(--muted); font-size: 0.82rem; margin-top: 0.1rem; }
.stat.good .stat-v { color: var(--ok-t); }
.stat.warn .stat-v { color: var(--flag-t); }
.stat.bad .stat-v { color: var(--bad-t); }

.rq {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 1rem;
  padding: 0.6rem 0;
  border-top: 1px solid var(--line);
}
.rq:first-of-type { border-top: 0; }
.rq-who b { display: block; font-weight: 600; font-size: 0.9rem; }
.rq-who span { color: var(--muted); font-size: 0.78rem; }
.rq-risk { display: flex; align-items: center; gap: 0.5rem; flex-shrink: 0; }

.meter {
  display: inline-block;
  width: 64px;
  height: 6px;
  border-radius: 99px;
  background: rgba(148, 163, 184, 0.18);
  overflow: hidden;
  vertical-align: middle;
}
.meter i { display: block; height: 100%; border-radius: 99px; }
.meter i.ok { background: var(--ok); }
.meter i.flag { background: var(--flag); }
.meter i.bad { background: var(--bad); }
.meter-n { font-size: 0.8rem; color: var(--soft); font-variant-numeric: tabular-nums; margin-left: 0.4rem; }

.kv { margin-bottom: 0.5rem; }
.kv > span:first-child { display: block; color: var(--muted); font-size: 0.78rem; margin-bottom: 0.1rem; }
.kv b { font-weight: 600; font-variant-numeric: tabular-nums; }

.lat { margin: 0.2rem 0 1rem 0; max-width: 34rem; }
.lat-top { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 0.45rem; }
.lat-top span { color: var(--muted); font-size: 0.84rem; }
.lat-top b { font-size: 1.7rem; font-weight: 500; letter-spacing: -0.03em; font-variant-numeric: tabular-nums; }
.lat-track { position: relative; height: 10px; border-radius: 99px; background: rgba(148, 163, 184, 0.16); }
.lat-track i { position: absolute; left: 0; top: 0; bottom: 0; border-radius: 99px; }
.lat-track i.ok { background: var(--ok); }
.lat-track i.bad { background: var(--bad); }
.lat-track u {
  position: absolute;
  top: -5px;
  bottom: -5px;
  width: 2px;
  background: var(--text);
  text-decoration: none;
}
.lat-foot { position: relative; height: 1.3rem; margin-top: 0.4rem; color: var(--muted); font-size: 0.76rem; }
.lat-foot span { position: absolute; transform: translateX(-50%); white-space: nowrap; }

.kpi-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0.75rem;
  margin: 0.2rem 0 1.2rem 0;
}
.kpi-grid.kpi-cols-4 { grid-template-columns: repeat(4, minmax(0, 1fr)); }

@media (max-width: 1100px) {
  .kpi-grid.kpi-cols-4 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}

.kpi {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 1rem 1.1rem 0.9rem 1.1rem;
  min-height: 108px;
  height: 100%;
  box-sizing: border-box;
  box-shadow: 0 1px 3px var(--shadow);
}
.kpi-label { font-size: 0.84rem; font-weight: 500; color: var(--muted); }
.kpi-value {
  font-size: 2rem;
  font-weight: 500;
  margin-top: 0.4rem;
  color: var(--text);
  letter-spacing: -0.035em;
  line-height: 1.1;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.kpi-hint { margin-top: 0.4rem; font-size: 0.8rem; color: var(--muted); line-height: 1.35; }
.kpi.kpi-compact { min-height: 90px; padding: 0.8rem 0.95rem; }
.kpi.kpi-compact .kpi-value { font-size: 1.5rem; }

.kpi.kpi-good .kpi-value, .kpi.health-up .kpi-value { color: var(--ok-t); }
.kpi.kpi-warn { border-color: rgba(255, 176, 32, 0.4); }
.kpi.kpi-warn .kpi-value { color: var(--flag-t); }
.kpi.health-down { border-color: rgba(255, 92, 112, 0.4); }
.kpi.health-down .kpi-value { color: var(--bad-t); }

.status-mix { display: flex; flex-direction: column; gap: 0.75rem; padding: 0.2rem 0 0.4rem; }
.status-mix-row { display: grid; grid-template-columns: 6.4rem 1fr 2.6rem; gap: 0.65rem; align-items: center; }
.status-mix-label { font-size: 0.82rem; color: var(--soft); font-weight: 500; }
.status-mix-track { height: 8px; border-radius: 99px; background: rgba(148, 163, 184, 0.14); overflow: hidden; }
.status-mix-track i { display: block; height: 100%; border-radius: 99px; min-width: 0; }
.status-mix-n { font-variant-numeric: tabular-nums; font-size: 0.82rem; color: var(--text); text-align: right; }
.status-mix-total { font-size: 0.78rem; color: var(--muted); margin-top: 0.15rem; }

.verify-donut {
  display: flex;
  align-items: center;
  justify-content: flex-start;
  gap: 1.5rem;
  padding: 0.5rem 0 0.85rem;
  min-height: 196px;
}

.verify-donut-stage {
  position: relative;
  width: 180px !important;
  height: 180px !important;
  min-width: 180px !important;
  min-height: 180px !important;
  max-width: 180px !important;
  max-height: 180px !important;
  flex: 0 0 180px !important;
  aspect-ratio: 1 / 1;
  overflow: hidden;
  border-radius: 50%;
}

.verify-donut-pie {
  position: absolute;
  inset: 0;
  width: 180px !important;
  height: 180px !important;
  border-radius: 50%;
  aspect-ratio: 1 / 1;
}

.verify-donut-hole {
  position: absolute;
  inset: 22%;
  border-radius: 50%;
  background: var(--surface);
  display: flex;
  align-items: center;
  justify-content: center;
}

.verify-donut-hole span {
  font-variant-numeric: tabular-nums;
  font-size: 1.2rem;
  font-weight: 600;
  color: var(--text);
  line-height: 1;
}

div[data-testid="stMarkdownContainer"] .verify-donut-stage,
div[data-testid="stMarkdownContainer"] .verify-donut-pie {
  width: 180px !important;
  height: 180px !important;
  max-width: 180px !important;
  max-height: 180px !important;
}

.verify-donut-legend {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.42rem;
  min-width: 9.5rem;
}

.verify-donut-legend li {
  display: grid;
  grid-template-columns: 0.7rem 1fr auto;
  gap: 0.5rem;
  align-items: center;
  font-size: 0.84rem;
  color: var(--soft);
}

.verify-donut-legend i { width: 0.7rem; height: 0.7rem; border-radius: 3px; display: inline-block; }
.verify-donut-legend b { font-variant-numeric: tabular-nums; color: var(--text); font-weight: 600; }

.att-legend {
  display: flex;
  gap: 1.35rem;
  align-items: center;
  margin: 0.1rem 0 0.45rem 0;
  font-size: 0.84rem;
  color: var(--soft);
}
.att-legend i {
  display: inline-block;
  width: 0.78rem;
  height: 0.78rem;
  border-radius: 3px;
  margin-right: 0.42rem;
  vertical-align: -1px;
}
.att-legend i.att-in { background: var(--ok); }
.att-legend i.att-enrolled { background: var(--track-strong); }

.badge {
  display: inline-block;
  padding: 0.1rem 0.5rem;
  border-radius: 999px;
  font-size: 0.76rem;
  font-weight: 500;
}
.badge-approved, .badge-active { color: var(--ok-t); background: rgba(34, 197, 94, 0.14); }
.badge-flagged, .badge-medium { color: var(--flag-t); background: rgba(245, 158, 11, 0.15); }
.badge-scheduled, .badge-pending { color: var(--wait-t); background: rgba(59, 130, 246, 0.14); }
.badge-rejected, .badge-cancelled, .badge-critical, .badge-high { color: var(--bad-t); background: rgba(239, 68, 68, 0.14); }
.badge-appealed { color: var(--appeal-t); background: rgba(167, 139, 250, 0.16); }
.badge-closed { color: var(--muted); background: rgba(138, 148, 163, 0.14); }
.badge-low { color: var(--ok-t); background: rgba(34, 197, 94, 0.14); }
.badge-approved, .badge-active, .badge-low { color: #166534; }
.badge-flagged, .badge-medium { color: #92400E; }

.stApp:has(.login-hero) .block-container {
  padding-top: 8vh !important;
  max-width: 440px !important;
}

.stApp:has(.login-hero) [data-testid="stForm"] {
  background: var(--surface) !important;
  border: 1px solid var(--line-strong) !important;
  border-radius: 16px !important;
  padding: 2.5rem 2rem !important;
  box-shadow: 0 24px 60px var(--shadow) !important;
  margin: 0 !important;
  width: 100% !important;
}

.login-hero { margin-bottom: 2rem; text-align: center; }
.login-mark { display: flex; justify-content: center; margin-bottom: 1rem; }
.login-mark .saiv-hex { width: 56px; height: 56px; }

.login-title {
  font-size: 2rem;
  font-weight: 600;
  letter-spacing: -0.045em;
  margin: 0 0 0.4rem 0;
  color: var(--text);
}

.login-lead {
  color: var(--muted);
  margin: 0 auto;
  font-size: 0.95rem;
  line-height: 1.5;
  max-width: 22rem;
}

.login-health { display: flex; justify-content: center; margin-top: 1.5rem; }
.login-health .health-row { justify-content: center; }

.stApp:has(.login-hero) section[data-testid="stSidebar"],
.stApp:has(.login-hero) [data-testid="collapsedControl"] {
  display: none !important;
  visibility: hidden !important;
  width: 0 !important;
  min-width: 0 !important;
  transform: translateX(-100%) !important;
}

.stApp:has(.login-hero) {
  background:
    radial-gradient(760px 340px at 50% -10%, var(--glow), transparent 60%),
    var(--bg);
}

.stTextInput input, .stNumberInput input, .stDateInput input, .stTimeInput input, .stTextArea textarea {
  background: transparent !important;
  border: 0 !important;
  box-shadow: none !important;
  color: var(--text) !important;
}

input::placeholder,
textarea::placeholder,
.stTextInput input::placeholder,
.stTextArea textarea::placeholder {
  color: var(--muted) !important;
  opacity: 1 !important;
}

.stTextInput label, .stSelectbox label, .stNumberInput label,
.stDateInput label, .stTimeInput label, .stTextArea label, .stCheckbox label {
  font-size: 0.82rem !important;
  font-weight: 500 !important;
  color: var(--muted) !important;
}

.stButton > button,
[data-testid="stFormSubmitButton"] > button {
  background: var(--btn-bg) !important;
  color: var(--btn-fg) !important;
  border: 0;
  font-weight: 600;
  font-size: 0.875rem;
  border-radius: 8px;
  padding: 0.45rem 1rem;
}

.stButton > button:hover,
[data-testid="stFormSubmitButton"] > button:hover {
  background: var(--btn-hover) !important;
  color: var(--btn-fg) !important;
  filter: none;
}

.stApp:has(.login-hero) [data-testid="stTextInput"]:has(input[type="password"]) {
  background: var(--input-bg) !important;
  border: 1px solid var(--line-strong) !important;
  border-radius: 8px !important;
  overflow: hidden !important;
  display: flex !important;
  align-items: center !important;
  padding: 0 !important;
  flex-direction: row !important;
}

.stApp:has(.login-hero) [data-testid="stTextInput"]:has(input[type="password"]) div[data-baseweb="base-input"] {
  background: transparent !important;
  border: none !important;
  flex-grow: 1 !important;
  margin: 0 !important;
  padding: 0 !important;
  width: 100% !important;
}

.stApp:has(.login-hero) [data-testid="stTextInput"]:has(input[type="password"]) input {
  background: transparent !important;
  border: none !important;
  color: var(--text) !important;
  padding: 0.55rem 0.8rem !important;
  outline: none !important;
  width: 100% !important;
}

.stApp:has(.login-hero) [data-testid="stTextInput"]:has(input[type="password"]) button {
  background: transparent !important;
  border: none !important;
  border-left: 1px solid var(--line) !important;
  border-radius: 0 !important;
  color: var(--muted) !important;
  padding: 0 0.8rem !important;
  height: 100% !important;
  display: flex !important;
  align-items: center !important;
  justify-content: center !important;
  margin: 0 !important;
}

.stApp:has(.login-hero) [data-testid="stTextInput"]:has(input[type="password"]) button:hover {
  color: var(--text) !important;
  background: var(--hover) !important;
}

div[data-testid="InputInstructions"],
div[data-testid="stTextInput"] small {
  display: none !important;
  visibility: hidden !important;
  height: 0 !important;
  margin: 0 !important;
  padding: 0 !important;
}

.empty strong {
  display: block !important;
  margin-bottom: 0.5rem !important;
}
.empty {
  line-height: 1.6 !important;
}

.table-wrapper {
  background: var(--surface) !important;
  border-radius: 10px;
  overflow: auto !important;
}

.custom-table {
  width: 100%;
  border-collapse: collapse;
  text-align: left;
}

.custom-table th {
  position: sticky;
  top: 0;
  background: var(--surface-2) !important;
  color: var(--text) !important;
  font-weight: 600;
  font-size: 0.85rem;
  padding: 0.75rem 1rem;
  border-bottom: 1px solid var(--line-strong);
  z-index: 10;
}

.custom-table td {
  padding: 0.75rem 1rem;
  border-bottom: 1px solid var(--line);
  color: var(--text) !important;
  font-size: 0.9rem;
}

.custom-table tr:hover {
  background: var(--hover) !important;
}

.custom-table tr:last-child td {
  border-bottom: none;
}

div[data-baseweb="popover"] ul,
div[data-baseweb="popover"] li {
    background-color: #FFFFFF !important;
}

div[data-baseweb="popover"] li {
    padding: 0.6rem 1rem !important;
    color: #0F172A !important;
    font-size: 0.9rem !important;
    cursor: pointer !important;
    border-bottom: 1px solid #F1F5F9 !important;
}

div[data-baseweb="popover"] li:hover {
    background-color: #F8FAFC !important;
    color: #0F172A !important;
}

div[data-testid="stTextInput"]:not(:has(input[type="password"])) div[data-baseweb="input"],
div[data-testid="stNumberInput"] div[data-baseweb="input"],
div[data-testid="stDateInput"] div[data-baseweb="input"],
div[data-testid="stTimeInput"] div[data-baseweb="select"] > div,
div[data-testid="stSelectbox"] div[data-baseweb="select"] > div,
div[data-testid="stTextArea"] div[data-baseweb="textarea"] {
  background-color: #FFFFFF !important;
  border: 1px solid #CBD5E1 !important;
  border-radius: 8px !important;
  min-height: 42px !important;
  color: #0F172A !important;
  box-shadow: none !important;
}

div[data-testid="stTextInput"]:not(:has(input[type="password"])) div[data-baseweb="base-input"],
div[data-testid="stNumberInput"] div[data-baseweb="base-input"],
div[data-testid="stDateInput"] div[data-baseweb="base-input"],
div[data-testid="stTextArea"] div[data-baseweb="base-input"] {
  background: transparent !important;
  border: 0 !important;
  border-radius: 8px !important;
}

div[data-testid="stTextInput"]:not(:has(input[type="password"])) div[data-baseweb="input"]:focus-within,
div[data-testid="stNumberInput"] div[data-baseweb="input"]:focus-within,
div[data-testid="stDateInput"] div[data-baseweb="input"]:focus-within,
div[data-testid="stTimeInput"] div[data-baseweb="select"] > div:focus-within,
div[data-testid="stSelectbox"] div[data-baseweb="select"] > div:focus-within,
div[data-testid="stTextArea"] div[data-baseweb="textarea"]:focus-within {
  border-color: #3B82F6 !important;
  box-shadow: 0 0 0 1px #3B82F6 !important;
}

div[data-testid="stTimeInput"] div[data-baseweb="select"] input {
  border: 0 !important;
  background: transparent !important;
  box-shadow: none !important;
  outline: none !important;
}

div[data-testid="stTabs"] [data-baseweb="tab-list"] {
  gap: 0.25rem;
  border-bottom: 1px solid var(--line-strong);
}

div[data-testid="stTabs"] button[role="tab"] {
  font-weight: 600;
  font-size: 0.92rem;
  color: var(--muted);
  padding: 0.65rem 1.1rem;
}

div[data-testid="stTabs"] button[role="tab"][aria-selected="true"] {
  color: var(--text);
}

div[data-testid="stTabs"] [data-baseweb="tab-highlight"] {
  background-color: #0F172A !important;
  height: 2px !important;
}

div[data-testid="stTabs"] [data-baseweb="tab-border"] {
  display: none !important;
}

.form-sec {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  margin: 1.5rem 0 0.7rem 0;
  font-size: 0.74rem;
  font-weight: 700;
  letter-spacing: 0.07em;
  text-transform: uppercase;
  color: var(--muted);
}

.form-sec::after {
  content: "";
  flex: 1;
  height: 1px;
  background: var(--line-strong);
}

.form-hint {
  color: var(--muted);
  font-size: 0.82rem;
  margin: -0.35rem 0 0.7rem 0;
}

.scope-note {
  color: var(--muted);
  font-size: 0.86rem;
  margin: -0.4rem 0 1rem 0;
}

.alert-strip {
  padding: 0.7rem 1rem;
  border-radius: 10px;
  font-size: 0.88rem;
  font-weight: 500;
  margin: 0 0 1rem 0;
  background: var(--surface);
  border: 1px solid var(--line-strong);
}

.alert-strip.danger {
  background: rgba(239, 68, 68, 0.08);
  border-color: rgba(239, 68, 68, 0.35);
  color: var(--bad-t);
}

.stDownloadButton > button {
  background: #FFFFFF !important;
  color: var(--text) !important;
  border: 1px solid #CBD5E1 !important;
  border-radius: 8px !important;
  font-weight: 600 !important;
  font-size: 0.875rem !important;
}

.stDownloadButton > button:hover {
  background: #F8FAFC !important;
  border-color: #94A3B8 !important;
  color: var(--text) !important;
}

div[class*="st-key-ok_"] .stButton > button,
div[data-testid="stElementContainer"]:has(.review-approve) + div[data-testid="stElementContainer"] button,
div[data-testid="element-container"]:has(.review-approve) + div[data-testid="element-container"] button {
  background: #15803D !important;
  color: #FFFFFF !important;
  border: 1px solid #15803D !important;
}

div[class*="st-key-ok_"] .stButton > button:hover,
div[data-testid="stElementContainer"]:has(.review-approve) + div[data-testid="stElementContainer"] button:hover,
div[data-testid="element-container"]:has(.review-approve) + div[data-testid="element-container"] button:hover {
  background: #166534 !important;
  border-color: #166534 !important;
  color: #FFFFFF !important;
}

div[class*="st-key-warn_"] .stButton > button {
  background: #B45309 !important;
  color: #FFFFFF !important;
  border: 1px solid #B45309 !important;
}

div[class*="st-key-warn_"] .stButton > button:hover {
  background: #92400E !important;
  border-color: #92400E !important;
  color: #FFFFFF !important;
}

div[class*="st-key-danger_"] .stButton > button,
div[data-testid="stElementContainer"]:has(.review-reject) + div[data-testid="stElementContainer"] button,
div[data-testid="element-container"]:has(.review-reject) + div[data-testid="element-container"] button {
  background: #DC2626 !important;
  color: #FFFFFF !important;
  border: 1px solid #DC2626 !important;
}

div[class*="st-key-danger_"] .stButton > button:hover,
div[data-testid="stElementContainer"]:has(.review-reject) + div[data-testid="stElementContainer"] button:hover,
div[data-testid="element-container"]:has(.review-reject) + div[data-testid="element-container"] button:hover {
  background: #B91C1C !important;
  border-color: #B91C1C !important;
  color: #FFFFFF !important;
}

div[class*="st-key-ghost_"] .stButton > button {
  background: #FFFFFF !important;
  color: var(--text) !important;
  border: 1px solid #CBD5E1 !important;
}

div[class*="st-key-ghost_"] .stButton > button:hover {
  background: #F1F5F9 !important;
  border-color: #94A3B8 !important;
  color: var(--text) !important;
}

.review-approve,
.review-reject {
  display: none !important;
}

div[data-testid="stElementContainer"]:has(.review-approve),
div[data-testid="stElementContainer"]:has(.review-reject),
div[data-testid="element-container"]:has(.review-approve),
div[data-testid="element-container"]:has(.review-reject) {
  height: 0 !important;
  min-height: 0 !important;
  margin: 0 !important;
  padding: 0 !important;
  overflow: hidden !important;
}

div[class*="st-key-act_btn"] button {
    background: #15803D !important;
    color: #FFFFFF !important;
    border: 1px solid #15803D !important;
}
div[class*="st-key-act_btn"] button:hover {
    background: #166534 !important;
    border-color: #166534 !important;
}

div[class*="st-key-deact_btn"] button {
    background: #B45309 !important;
    color: #FFFFFF !important;
    border: 1px solid #B45309 !important;
}
div[class*="st-key-deact_btn"] button:hover {
    background: #92400E !important;
    border-color: #92400E !important;
}

div[class*="st-key-del_btn"] button {
    background: #DC2626 !important;
    color: #FFFFFF !important;
    border: 1px solid #DC2626 !important;
}
div[class*="st-key-del_btn"] button:hover {
    background: #B91C1C !important;
    border-color: #B91C1C !important;
}

.stButton > button:disabled,
.stButton > button[disabled] {
  opacity: 0.4 !important;
  cursor: not-allowed !important;
  box-shadow: none !important;
}

.btn-mark { display: none !important; }

div[data-testid="stElementContainer"]:has(.btn-mark),
div[data-testid="element-container"]:has(.btn-mark) {
  height: 0 !important;
  min-height: 0 !important;
  margin: 0 0 -1rem 0 !important;
  padding: 0 !important;
  overflow: hidden !important;
}

div[data-testid="stElementContainer"]:has(.btn-ok) + div[data-testid="stElementContainer"] button,
div[data-testid="element-container"]:has(.btn-ok) + div[data-testid="element-container"] button {
  background: #15803D !important;
  color: #FFFFFF !important;
  border: 1px solid #15803D !important;
}
div[data-testid="stElementContainer"]:has(.btn-ok) + div[data-testid="stElementContainer"] button:hover,
div[data-testid="element-container"]:has(.btn-ok) + div[data-testid="element-container"] button:hover {
  background: #166534 !important;
  border-color: #166534 !important;
  color: #FFFFFF !important;
}

div[data-testid="stElementContainer"]:has(.btn-warn) + div[data-testid="stElementContainer"] button,
div[data-testid="element-container"]:has(.btn-warn) + div[data-testid="element-container"] button {
  background: #B45309 !important;
  color: #FFFFFF !important;
  border: 1px solid #B45309 !important;
}
div[data-testid="stElementContainer"]:has(.btn-warn) + div[data-testid="stElementContainer"] button:hover,
div[data-testid="element-container"]:has(.btn-warn) + div[data-testid="element-container"] button:hover {
  background: #92400E !important;
  border-color: #92400E !important;
  color: #FFFFFF !important;
}

div[data-testid="stElementContainer"]:has(.btn-danger) + div[data-testid="stElementContainer"] button,
div[data-testid="element-container"]:has(.btn-danger) + div[data-testid="element-container"] button {
  background: #DC2626 !important;
  color: #FFFFFF !important;
  border: 1px solid #DC2626 !important;
}
div[data-testid="stElementContainer"]:has(.btn-danger) + div[data-testid="stElementContainer"] button:hover,
div[data-testid="element-container"]:has(.btn-danger) + div[data-testid="element-container"] button:hover {
  background: #B91C1C !important;
  border-color: #B91C1C !important;
  color: #FFFFFF !important;
}

div[data-testid="stElementContainer"]:has(.btn-info) + div[data-testid="stElementContainer"] button,
div[data-testid="element-container"]:has(.btn-info) + div[data-testid="element-container"] button {
  background: #3B82F6 !important;
  color: #FFFFFF !important;
  border: 1px solid #3B82F6 !important;
}
div[data-testid="stElementContainer"]:has(.btn-info) + div[data-testid="stElementContainer"] button:hover,
div[data-testid="element-container"]:has(.btn-info) + div[data-testid="element-container"] button:hover {
  background: #2563EB !important;
  border-color: #2563EB !important;
  color: #FFFFFF !important;
}

.adm-stats {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 0.75rem;
  margin: 0.4rem 0 1.4rem 0;
}
.adm-card {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 0.85rem 1.1rem;
}
.adm-v {
  font-size: 1.65rem;
  font-weight: 600;
  letter-spacing: -0.03em;
  font-variant-numeric: tabular-nums;
  line-height: 1.15;
}
.adm-l {
  color: var(--muted);
  font-size: 0.78rem;
  font-weight: 500;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  margin-top: 0.2rem;
}
.adm-card.good .adm-v { color: var(--ok-t); }
@media (max-width: 900px) {
  .adm-stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
/* ===== Sidebar polish ===== */
section[data-testid="stSidebar"] {
  background: linear-gradient(180deg, #0F172A 0%, #111C33 100%) !important;
  border-right: 1px solid rgba(148, 163, 184, 0.12) !important;
}
section[data-testid="stSidebar"] button[kind="secondary"],
section[data-testid="stSidebar"] [data-testid="stBaseButton-secondary"] {
  color: #CBD5E1 !important;
}
section[data-testid="stSidebar"] button[kind="primary"],
section[data-testid="stSidebar"] [data-testid="stBaseButton-primary"] {
  background: rgba(34, 197, 94, 0.12) !important;
  border: 1px solid rgba(34, 197, 94, 0.28) !important;
  box-shadow: inset 3px 0 0 #22C55E !important;
  color: #FFFFFF !important;
}
section[data-testid="stSidebar"] [data-testid="stElementContainer"]:has(.workspace-tools) ~ [data-testid="stElementContainer"][class*="st-key-sidebar_logout"] button {
  background: transparent !important;
  border: 1px solid rgba(248, 113, 113, 0.45) !important;
  color: #FCA5A5 !important;
}
section[data-testid="stSidebar"] [data-testid="stElementContainer"]:has(.workspace-tools) ~ [data-testid="stElementContainer"][class*="st-key-sidebar_logout"] button:hover {
  background: rgba(248, 113, 113, 0.12) !important;
  color: #FFFFFF !important;
}
section[data-testid="stSidebar"] [data-testid="stElementContainer"]:has(.workspace-tools) ~ [data-testid="stElementContainer"][class*="st-key-sidebar_refresh"] button {
  background: transparent !important;
  border: 1px solid #334155 !important;
  color: #CBD5E1 !important;
}
.user-chip { border-radius: 12px !important; }

section[data-testid="stSidebar"] [data-testid="stElementContainer"]:has(.nav-label) {
  margin-bottom: 0.35rem !important;
}

.grafana-title {
  font-size: 0.86rem;
  font-weight: 600;
  color: var(--soft);
  margin: 0.4rem 0 0.3rem 0;
}
.grafana-open { font-size: 0.84rem; margin: 0.4rem 0 1rem 0; }
.grafana-open a { color: var(--wait-t); text-decoration: none; font-weight: 600; }
.week-label {
  text-align: center;
  font-weight: 600;
  color: var(--text);
  padding-top: 0.55rem;
}

/* ===== Buttons =====
   dark = main action, green = confirm / create, amber = caution,
   red = destructive, outline (ghost_) = secondary */
.stButton > button { min-height: 2.5rem; }

/* ===== Layout fixes ===== */

/* 1. Zero-height helpers (hidden iframes, <style> injectors) still created an
      element container, and each container adds a 1rem flex gap. Remove them. */
[data-testid="stElementContainer"]:has(> iframe[height="0"]),
[data-testid="stElementContainer"]:has(> div > iframe[height="0"]),
[data-testid="stElementContainer"]:has(> [data-testid="stCustomComponentV1"] iframe[height="0"]),
[data-testid="stElementContainer"]:has(> [data-testid="stIFrame"] iframe[height="0"]),
[data-testid="stElementContainer"]:has(> [data-testid="stMarkdown"] style),
[data-testid="element-container"]:has(> iframe[height="0"]),
[data-testid="element-container"]:has(> div > iframe[height="0"]),
[data-testid="element-container"]:has(> [data-testid="stMarkdown"] style) {
  display: none !important;
}

/* 2. Main content: consistent top padding on every page. */
[data-testid="stMainBlockContainer"],
.block-container {
  padding-top: 1.5rem !important;
}

/* 3. Sidebar: remove Streamlit's reserved header + 6rem top padding,
      lock the width so it does not shift between pages. */
section[data-testid="stSidebar"] {
  width: 280px !important;
}
[data-testid="stSidebarHeader"] {
  display: none !important;
  height: 0 !important;
  padding: 0 !important;
  margin: 0 !important;
}
[data-testid="stSidebarUserContent"] {
  padding-top: 0.25rem !important;
  padding-bottom: 1rem !important;
}

/* 4. Sidebar nav: tight, even rhythm (was 1rem gap + 0.22rem margin per button). */
section[data-testid="stSidebar"] [data-testid="stVerticalBlock"] {
  gap: 0.3rem !important;
}
section[data-testid="stSidebar"] .stButton {
  margin-bottom: 0 !important;
}
section[data-testid="stSidebar"] [data-testid="stElementContainer"]:has(.workspace-tools) ~ [data-testid="stElementContainer"] .stButton {
  margin-bottom: 0 !important;
}

/* 5. Tables: keep the risk meter and its number on one line, centre cells vertically. */
.custom-table td { vertical-align: middle; }
.custom-table td:has(.meter) { white-space: nowrap; }
.custom-table td .meter,
.custom-table td .meter-n { vertical-align: middle; }

/* 6. Slider: use the UI font instead of the green monospace default. */
[data-testid="stSliderThumbValue"],
[data-testid="stSliderTickBarMin"],
[data-testid="stSliderTickBarMax"] {
  font-family: inherit !important;
  color: var(--muted) !important;
}

/* 7. Filter-row checkbox: line it up with the input boxes next to it. */
div[class*="st-key-checkins_queue_only"] {
  margin-top: 1.95rem;
}

"""

def inject_css() -> None:
    st.markdown(f"<style>{CSS_LIGHT}{CSS_BASE}</style>", unsafe_allow_html=True)
    
    if not st.session_state.get("access_token"):
        st.markdown(
            '<div class="saiv-login-mode" style="display:none" aria-hidden="true"></div>',
            unsafe_allow_html=True,
        )

def _mark_cells() -> str:
    fills = ["ok", "ok", "ok", "ok", "ok", "flag", "wait", "off", "off"]
    out = []
    for i, kind in enumerate(fills):
        x = 11 + (i % 3) * 11
        y = 11 + (i // 3) * 11
        if kind == "ok":
            out.append(f'<rect x="{x}" y="{y}" width="7" height="7" rx="1.6" fill="#22C55E"/>')
        elif kind == "flag":
            out.append(f'<rect x="{x}" y="{y}" width="7" height="7" rx="1.6" fill="#F59E0B"/>')
        elif kind == "wait":
            out.append(
                f'<rect x="{x + 0.75}" y="{y + 0.75}" width="5.5" height="5.5" rx="1.2" '
                f'fill="none" stroke="#3B82F6" stroke-width="1.5"/>'
            )
        else:
            out.append(
                f'<rect x="{x + 0.5}" y="{y + 0.5}" width="6" height="6" rx="1.4" '
                f'fill="none" stroke="rgba(148,163,184,0.45)" stroke-width="1"/>'
            )
    return "".join(out)

MARK_SVG = (
    '<svg class="saiv-hex" viewBox="0 0 48 48" fill="none" xmlns="http://www.w3.org/2000/svg">'
    '<rect x="2" y="2" width="44" height="44" rx="11" fill="#12161C" '
    'stroke="rgba(148,163,184,0.3)" stroke-width="1.2"/>'
    + _mark_cells()
    + "</svg>"
)