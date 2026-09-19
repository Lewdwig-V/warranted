python - <<'PY'
import csv
import json
from collections import Counter
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

offsets = list(Path('.').glob('offset-v*'))
assert len(offsets) == 1
zone = timezone(timedelta(minutes=json.loads(offsets[0].read_text())['minutes']))
rows = []
with Path('input.csv').open() as source:
    for row in csv.DictReader(source):
        timestamp = datetime.fromisoformat(row['timestamp']).replace(tzinfo=zone)
        rows.append([row['id'], timestamp.astimezone(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'), int(row['value'])])
totals = Counter()
for _, timestamp, value in rows:
    totals[timestamp[:10]] += value
Path('result.json').write_text(json.dumps({'rows': rows, 'totals': dict(totals)}))
PY
if [ $? -ne 0 ]; then exit 1; fi
printf 'COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT\nresult.json\n'
