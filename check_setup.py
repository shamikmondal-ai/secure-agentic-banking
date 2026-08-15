"""Environment sanity check for the Anthropic API setup used by this project.

Verifies: the anthropic package is installed, ANTHROPIC_API_KEY is present
(loaded from .env, never printed), a live "reply with OK" call succeeds, and
prints whatever account/org identifiers the API response exposes.
"""

import os
import sys

MODEL = "claude-opus-5"


def check_package():
    try:
        import anthropic
    except ImportError:
        print("[FAIL] anthropic package is not installed")
        sys.exit(1)
    print(f"[OK] anthropic package installed (version {anthropic.__version__})")
    return anthropic


def check_api_key():
    from dotenv import load_dotenv

    load_dotenv()
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        print("[FAIL] ANTHROPIC_API_KEY not found (checked .env and environment)")
        sys.exit(1)
    print(f"[OK] ANTHROPIC_API_KEY present ({len(key)} characters; value not printed)")


def check_live_call(anthropic_module):
    client = anthropic_module.Anthropic()
    raw_response = client.messages.with_raw_response.create(
        model=MODEL,
        max_tokens=16,
        messages=[{"role": "user", "content": "reply with OK"}],
    )
    message = raw_response.parse()
    reply_text = next((b.text for b in message.content if b.type == "text"), "")
    print(f"[OK] Live API call succeeded. Reply: {reply_text!r}")

    print(f"[INFO] request_id: {message._request_id}")

    org_like_headers = {
        k: v for k, v in raw_response.headers.items()
        if "org" in k.lower() or "workspace" in k.lower() or "account" in k.lower()
    }
    if org_like_headers:
        print("[INFO] Account/org headers on the response:")
        for k, v in org_like_headers.items():
            print(f"    {k}: {v}")
    else:
        print(
            "[INFO] No account/org identifier is exposed by the Messages API response "
            "(that's an Admin API / Console concept, not returned here)."
        )


def main():
    anthropic_module = check_package()
    check_api_key()
    check_live_call(anthropic_module)
    print("\nEnvironment looks ready.")


if __name__ == "__main__":
    main()
