"""Sample runner: process one transaction through the coordinator agent."""

import json

from coordinator import process_transaction

SAMPLE_TRANSACTION = {
    "sender_name": "Elena Marchetti",
    "customer_id": "CUST-1003",
    "amount": 25000.00,
}

if __name__ == "__main__":
    result = process_transaction(**SAMPLE_TRANSACTION)
    print(json.dumps(result, indent=2))
