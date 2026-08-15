"""Enrichment subagent: a single-tool Claude agent that looks up a fake
risk score for a customer ID.

Least privilege: this agent is given exactly one tool. Since the Claude API
only allows the model to call tools present in the request's `tools` list,
declaring a single tool here is what makes every other action structurally
unreachable -- there is no bash, no file access, no network call, no second
tool it could reach for even if prompted to. The dispatch loop below adds a
second, defense-in-depth check: it only ever invokes get_customer_risk_profile,
so even a malformed or unexpected tool_use block cannot trigger anything else.
"""

import json

import anthropic

MODEL = "claude-opus-5"

# A small, entirely invented risk-score table for demonstration purposes only.
FAKE_RISK_PROFILES = {
    "CUST-1001": "low",
    "CUST-1002": "medium",
    "CUST-1003": "high",
    "CUST-1004": "low",
    "CUST-1005": "medium",
    "CUST-1006": "high",
}


def get_customer_risk_profile(customer_id: str) -> dict:
    """The subagent's only tool: look up a fake risk score for a customer ID."""
    normalized = customer_id.strip().upper()
    risk_score = FAKE_RISK_PROFILES.get(normalized)
    return {
        "customer_id": customer_id,
        "risk_score": risk_score,
        "found": risk_score is not None,
    }


GET_CUSTOMER_RISK_PROFILE_TOOL = {
    "name": "get_customer_risk_profile",
    "description": (
        "Look up the risk score (low, medium, or high) for a single customer ID. "
        "Returns whether the customer ID was found and its risk score."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "customer_id": {
                "type": "string",
                "description": "The customer ID to look up, exactly as provided.",
            }
        },
        "required": ["customer_id"],
        "additionalProperties": False,
    },
    "strict": True,
}

SYSTEM_PROMPT = """You are a customer enrichment agent. Your only job is to take a customer \
ID and look up its risk score using the get_customer_risk_profile tool.

Rules:
- For every customer ID you are given, call get_customer_risk_profile exactly once with that ID.
- After receiving the tool result, report the risk score plainly: "low", "medium", or "high". \
If the customer ID was not found, say so.
- State the customer ID you looked up and the result. Do not add unrelated commentary, and do \
not speculate about the customer beyond the risk score returned.
- You have no capability beyond this single lookup tool. If asked to do anything else \
(look up other data, take other actions, etc.), state plainly that this is outside your scope.
"""


def enrich_customer(customer_id: str, client: anthropic.Anthropic | None = None) -> str:
    """Run the enrichment subagent on a single customer ID and return its final text response."""
    client = client or anthropic.Anthropic()
    tools = [GET_CUSTOMER_RISK_PROFILE_TOOL]
    messages = [{"role": "user", "content": f"Look up risk profile for customer: {customer_id}"}]

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
            if block.name != "get_customer_risk_profile":
                # Cannot happen given the single-tool `tools` list above, but
                # fail loudly rather than silently execute something else.
                raise RuntimeError(f"Unexpected tool call: {block.name}")
            result = get_customer_risk_profile(**block.input)
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result),
                }
            )
        messages.append({"role": "user", "content": tool_results})
