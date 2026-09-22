"""The new consumer's observable configuration."""


def connection(settings):
    return settings["endpoint"], settings["timeout_seconds"]
