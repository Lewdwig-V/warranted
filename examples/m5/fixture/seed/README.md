# Configuration migration: initial contract

Change only `migrate.py`. Read one JSON object from stdin and write one JSON object to stdout.
Convert version 1 to version 2. Rename `host` to `endpoint` and `timeout` to `timeout_seconds`.
Both timeout fields use seconds. Remove the old names and set `version` to integer 2.
Preserve the host and timeout values. Preserve the optional label's presence and exact string value.
The two consumers define behavior that the migration must preserve.
An absent label selects `"default"`. An empty label stays empty.

Version 1 requires a nonempty string host and a nonnegative integer timeout.
The optional label must be a string. Reject booleans as integers, duplicate JSON keys, and extra fields.
Reject invalid configurations with a nonzero exit code and no stdout.
Versions other than 1 and 2 are unsupported.
The initial contract does not require the program to accept version 2 input.
Only whitespace and JSON object key order can differ in accepted output.

Run the public example inside the contained workspace with `python example_test.py`.
It does not replace the host's independent assessment.
The host delivers an approved requirement revision after assessing the first submission.
