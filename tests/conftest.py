import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

import os
# No test may find a real Anthropic API key or touch the Keychain: the AI engine gets a fake client
# (tests/test_ai_engine.py) or none at all.
os.environ["LIVE_AI_NO_KEYCHAIN"] = "1"
os.environ["LIVE_AI_NO_DOTENV"] = "1"
os.environ.pop("ANTHROPIC_API_KEY", None)
