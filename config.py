import os
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

try:
    ANTHROPIC_API_KEY = st.secrets["ANTHROPIC_API_KEY"]
except Exception:
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

DATABASE_URL           = os.getenv("DATABASE_URL",
                                   "sqlite:///./compiler.db")
MAX_REPAIR_ATTEMPTS    = int(os.getenv("MAX_REPAIR_ATTEMPTS", "3"))
CLAUDE_MODEL           = "claude-sonnet-4-20250514"
CLAUDE_TEMPERATURE     = float(os.getenv("CLAUDE_TEMPERATURE", "0.2"))
CLAUDE_MAX_TOKENS      = int(os.getenv("CLAUDE_MAX_TOKENS", "4000"))
CLAUDE_TEMPERATURE_REPAIR = float(
    os.getenv("CLAUDE_TEMPERATURE_REPAIR", "0.1")
)
