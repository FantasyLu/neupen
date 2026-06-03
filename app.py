"""
Neupen — 应用入口
st.set_page_config 必须是第一个 Streamlit 调用，因此放在此处
"""

import streamlit as st

st.set_page_config(
    page_title="Neupen",
    page_icon="✒️",
    layout="wide",
    initial_sidebar_state="expanded",
)

from ui.app import main

main()
