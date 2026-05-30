import streamlit as st

st.set_page_config(layout="wide", page_title="ZEMO.ai")

from app import render_compiler
from build_frontend import render_studio

# Must be the absolute first Streamlit command run


# Safely initialize the view routing key
if "current_view" not in st.session_state:
    st.session_state.current_view = "compiler"

# Route to the appropriate view function
if st.session_state.current_view == "compiler":
    render_compiler()
elif st.session_state.current_view == "studio":
    render_studio()
