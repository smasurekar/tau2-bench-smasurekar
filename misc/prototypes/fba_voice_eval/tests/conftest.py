"""Make the prototype modules importable and keep the tests offline."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# tau2_ihub_overrides builds an OpenAI client at import; the tests never call it.
os.environ.setdefault("OPENAI_API_KEY", "sk-offline-test")
