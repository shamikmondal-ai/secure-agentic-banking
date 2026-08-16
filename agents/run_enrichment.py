"""Sample runner: enrich one customer ID through the enrichment subagent."""

import json

from enrichment import enrich_customer

SAMPLE_CUSTOMER_ID = "CUST-1003"

if __name__ == "__main__":
    result = enrich_customer(SAMPLE_CUSTOMER_ID)
    print(json.dumps(result, indent=2))
