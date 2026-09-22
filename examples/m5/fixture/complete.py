"""Fixed successful proposal; two literal flag changes supply the negative cases."""

import json
import sys

ACCEPT_CURRENT = True
KEEP_LABEL = True


def unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def migrate(value):
    if type(value) is not dict or type(value.get("version")) is not int:
        raise ValueError("expected an integer version")
    version = value["version"]
    if version not in (1, 2):
        raise ValueError("unsupported version")
    host, timeout = (
        ("host", "timeout") if version == 1 else ("endpoint", "timeout_seconds")
    )
    required = {"version", host, timeout}
    if (
        not required <= value.keys() <= required | {"label"}
        or type(value[host]) is not str
        or not value[host]
        or type(value[timeout]) is not int
        or value[timeout] < 0
        or ("label" in value and type(value["label"]) is not str)
    ):
        raise ValueError("invalid configuration")
    if version == 2:
        if not ACCEPT_CURRENT:
            raise ValueError("only version 1 is accepted")
        return value
    result = {"version": 2, "endpoint": value[host], "timeout_seconds": value[timeout]}
    if KEEP_LABEL and "label" in value:
        result["label"] = value["label"]
    return result


if __name__ == "__main__":
    print(
        json.dumps(
            migrate(json.load(sys.stdin, object_pairs_hook=unique)), allow_nan=False
        )
    )
