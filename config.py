import os
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY must be set in your .env file.")

DATABASE_URL           = os.getenv("DATABASE_URL",
                                   "sqlite:///./compiler.db")
MAX_REPAIR_ATTEMPTS    = int(os.getenv("MAX_REPAIR_ATTEMPTS", "3"))
GEMINI_MODEL           = "gemini-2.5-flash"
GEMINI_TEMPERATURE     = float(os.getenv("GEMINI_TEMPERATURE", "0.2"))
GEMINI_MAX_OUTPUT_TOKENS = int(os.getenv("GEMINI_MAX_OUTPUT_TOKENS", "65536"))
GEMINI_TEMPERATURE_REPAIR = float(
    os.getenv("GEMINI_TEMPERATURE_REPAIR", "0.1")
)
