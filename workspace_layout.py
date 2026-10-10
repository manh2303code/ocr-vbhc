"""Small, deterministic workspace layout rules shared by UI and tests."""


def should_collapse_details(width: int, chat_visible: bool) -> bool:
    """Keep the editor out of the way when it would create four thin columns."""
    return width < 1200 or (chat_visible and width < 1600)


def splitter_sizes(width: int, library_visible: bool, details_visible: bool):
    width = max(0, int(width))
    left = min(280 if width >= 1366 else 250, width) if library_visible else 0
    right = min(380, max(0, width - left)) if details_visible else 0
    return [left, max(0, width - left - right), right]
