"""Ask the host to run mystery.py on each argument: python probe.py INPUT [INPUT ...]

The host runs the original program in a contained job, records the result as
evidence, and shows it in the next observation. Batch several inputs in one call.
"""

import json
import sys

print("WARRANTED_REQUEST")
print(
    json.dumps(
        {
            "requests": [
                {"operation": "probe", "arguments": {"input": text}}
                for text in sys.argv[1:]
            ]
        }
    )
)
