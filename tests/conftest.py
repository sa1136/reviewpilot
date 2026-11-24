"""Tests never talk to LangSmith: tracing is forced off before .env can turn it on
(load_dotenv never overrides a variable that is already set)."""

import os

os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"
