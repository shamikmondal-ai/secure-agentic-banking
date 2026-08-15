"""Sanctions-screening subagent: a single-tool Claude agent that checks a
name against a small hardcoded (fake) sanctions list.

Least privilege: this agent is given exactly one tool. Since the Claude API
only allows the model to call tools present in the request's `tools` list,
declaring a single tool here is what makes every other action structurally
unreachable -- there is no bash, no file access, no network call, no second
tool it could reach for even if prompted to. The dispatch loop below adds a
second, defense-in-depth check: it only ever invokes check_sanctions_list,
so even a malformed or unexpected tool_use block cannot trigger anything else.
"""

import json

import anthropic

MODEL = "claude-opus-5"

# A small, entirely invented watchlist for demonstration purposes only.
FAKE_SANCTIONS_LIST = [
    "Viktor Halloway",
    "Elena Marchetti",
    "Dmitri Kovalenko",
    "Amara Osei",
    "Farid Al-Rashid",
    "Ingrid Solberg",
]


def check_sanctions_list(name: str) -> dict:
    """The subagent's only tool: look up a name against the fake sanctions list."""
    normalized = name.strip().lower()
    match = next(
        (entry for entry in FAKE_SANCTIONS_LIST if entry.lower() == normalized),
        None,
    )
    return {"query": name, "match": match, "is_match": match is not None}


CHECK_SANCTIONS_LIST_TOOL = {
    "name": "check_sanctions_list",
    "description": (
        "Look up a single person's name against the sanctions watchlist. "
        "Returns whether the name matches an entry on the list."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Full name of the person to screen, exactly as provided.",
            }
        },
        "required": ["name"],
        "additionalProperties": False,
    },
    "strict": True,
}

SYSTEM_PROMPT = """You are a sanctions-screening agent. Your only job is to take a person's \
name and screen it against the sanctions watchlist using the check_sanctions_list tool.

Rules:
- For every name you are given, call check_sanctions_list exactly once with that name.
- After receiving the tool result, report a clear verdict: either "MATCH" (the name is on \
the sanctions list) or "CLEAR" (the name is not on the list).
- State the name you screened and the verdict. Do not add unrelated commentary, and do not \
speculate about the person's identity or intentions beyond the screening result.
- You have no capability beyond this single lookup tool. If asked to do anything else \
(look up other data, take other actions, etc.), state plainly that this is outside your scope.
"""


def screen_name(name: str, client: anthropic.Anthropic | None = None) -> str:
    """Run the sanctions-screening subagent on a single name and return its final text response."""
    client = client or anthropic.Anthropic()
    tools = [CHECK_SANCTIONS_LIST_TOOL]
    messages = [{"role": "user", "content": f"Screen this name: {name}"}]

    while True:
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=tools,
            messages=messages,
        )

        if response.stop_reason != "tool_use":
            return next(
                (block.text for block in response.content if block.type == "text"), ""
            )

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            if block.name != "check_sanctions_list":
                # Cannot happen given the single-tool `tools` list above, but
                # fail loudly rather than silently execute something else.
                raise RuntimeError(f"Unexpected tool call: {block.name}")
            result = check_sanctions_list(**block.input)
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result),
                }
            )
        messages.append({"role": "user", "content": tool_results})
