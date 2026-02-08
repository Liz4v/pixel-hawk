import pytest
from loguru import logger


@pytest.fixture(autouse=True)
def disable_file_logging(monkeypatch):
    """Prevent logger.add() from creating file handlers during tests."""
    original_add = logger.add

    def mock_add(sink, **kwargs):
        # Only allow non-file sinks (like sys.stderr which is the default)
        # Block file path sinks to prevent log files during tests
        if hasattr(sink, "__fspath__") or isinstance(sink, (str, bytes)):
            return None  # Return dummy handler ID
        return original_add(sink, **kwargs)

    monkeypatch.setattr(logger, "add", mock_add)
    yield
