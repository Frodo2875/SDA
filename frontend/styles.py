"""Scoped presentation tokens; Streamlit owns widgets, focus and theme selection."""

import streamlit as st


def render_styles() -> None:
    """Apply a single design system without changing widget or application state."""
    st.markdown(STYLES, unsafe_allow_html=True)


# Derive borders and surfaces from inherited currentColor. Streamlit owns the
# foreground/background, so theme changes work without Python theme detection.
# Selectors target our keyed containers; native test IDs are used only for widgets
# that Streamlit cannot style through its public Python API.
STYLES = """
<style>
.stApp {
  --sda-accent: #315ee7;
  --sda-border: color-mix(in srgb, currentColor 16%, transparent);
  --sda-soft: color-mix(in srgb, currentColor 4%, transparent);
  font-family: Inter, "Noto Sans SC", "Microsoft YaHei", sans-serif;
}
.stApp .block-container {max-width:1480px; padding:4rem 2rem 3rem;}
.stApp h1 {font-size:1.9rem; letter-spacing:-.035em; padding-top:.3rem;}
.stApp h2 {font-size:1.35rem;}
.stApp h3 {font-size:1.12rem;}
.stApp p, .stApp li {line-height:1.75;}
.stApp button {border-radius:8px; transition:border-color 150ms, background-color 150ms;}
.stApp button:focus-visible {outline:2px solid var(--sda-accent); outline-offset:3px;}
.stApp button:disabled {cursor:not-allowed;}
.stApp button[kind="primary"] {background:var(--sda-accent); border-color:var(--sda-accent); color:white;}
.stApp [data-testid="stSidebar"] {border-right:1px solid var(--sda-border);}
.st-key-sda-navigation button {justify-content:flex-start; min-height:2.75rem; border-color:transparent;}
.st-key-sda-navigation button[kind="secondary"] {background:transparent;}
.st-key-sda-navigation {gap:.35rem;}
.st-key-sda-navigation button[kind="secondary"]:hover {background:var(--sda-soft); border-color:var(--sda-border);}
.platform-brand {font-size:1.05rem; font-weight:650; letter-spacing:-.02em;}
.st-key-sda-header {padding-bottom:.4rem; border-bottom:1px solid var(--sda-border); margin-bottom:.8rem;}
.stApp [data-testid="stMetric"] {border:1px solid var(--sda-border); border-radius:12px; padding:.85rem 1rem; background:var(--sda-soft);}
.stApp [data-testid="stVerticalBlockBorderWrapper"] {border-radius:12px;}
.st-key-sda-chat {max-width:960px; margin-inline:auto;}
.st-key-sda-chat [data-testid="stChatMessage"] {border-radius:16px; padding:1rem;}
.st-key-sda-chat [data-testid="stChatMessage"] p {overflow-wrap:anywhere;}
.st-key-sda-chat [data-testid="stChatMessage"] pre {max-width:100%; overflow-x:auto;}
.st-key-sda-chat [data-testid="stMarkdownContainer"] table {display:block; max-width:100%; overflow-x:auto;}
.st-key-sda-chat-tools {margin-bottom:.5rem;}
.st-key-sda-composer {padding-top:.75rem; border-top:1px solid var(--sda-border);}
.st-key-sda-composer [data-testid="stSelectbox"] {min-width:0;}
.st-key-sda-file-toolbar {padding:.75rem; border:1px solid var(--sda-border); border-radius:12px; background:var(--sda-soft);}
.sda-source-highlight {border-left:3px solid var(--sda-accent); padding:.6rem 1rem; background:var(--sda-soft); overflow-wrap:anywhere;}
.stApp [data-testid="stCaptionContainer"] {overflow-wrap:anywhere;}
.stApp [data-testid="stExpander"] summary {min-height:2.65rem;}
@media (max-width:900px) {
  .stApp .block-container {padding-inline:1.1rem;}
  .st-key-sda-header [data-testid="stHorizontalBlock"] {flex-wrap:wrap;}
}
@media (max-width:640px) {
  .stApp .block-container {padding:3.5rem .8rem 2rem;}
  .stApp h1 {font-size:1.55rem;}
  .st-key-sda-composer [data-testid="stHorizontalBlock"] {flex-wrap:nowrap; gap:.5rem;}
  .st-key-sda-composer [data-testid="stColumn"]:first-child {min-width:7.2rem; flex:0 0 7.2rem;}
  .st-key-sda-composer [data-testid="stColumn"]:last-child {min-width:0; flex:1 1 0;}
  .st-key-sda-chat [data-testid="stChatMessage"] {padding:.6rem;}
}
@media (prefers-reduced-motion:reduce) {
  .stApp button {transition:none;}
}
</style>
"""
