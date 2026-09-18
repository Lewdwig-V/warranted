"""The trusted, fixed M1 transformation; no access to evaluator answers needed."""

import csv
import json
from datetime import UTC, datetime
from pathlib import Path

# The test observes launches outside the ledger's claim of completion.
with Path("executions.log").open("ab") as counter:
    counter.write(b"executed\n")

totals = {}
with (
    Path("input.csv").open(newline="", encoding="utf-8") as source,
    Path("normalized.csv").open("w", newline="", encoding="utf-8") as output,
):
    rows = csv.DictReader(source)
    writer = csv.DictWriter(
        output, fieldnames=["id", "timestamp", "value"], lineterminator="\n"
    )
    writer.writeheader()
    for row in rows:
        timestamp = datetime.fromisoformat(row["timestamp"])
        if timestamp.tzinfo is None:
            raise ValueError("timestamp requires an explicit UTC offset")
        timestamp = timestamp.astimezone(UTC)
        day = timestamp.date().isoformat()
        totals[day] = totals.get(day, 0) + int(row["value"])
        row["timestamp"] = timestamp.isoformat().replace("+00:00", "Z")
        writer.writerow(row)
Path("totals.json").write_text(
    json.dumps(totals, sort_keys=True) + "\n", encoding="utf-8"
)
print("normalized rows and UTC daily totals written")
