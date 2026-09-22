"""The legacy consumer distinguishes an empty label from an absent label."""


def label(settings):
    return settings.get("label", "default")
