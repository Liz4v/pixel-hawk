"""Tests for configuration management."""

from pathlib import Path


import pixel_hawk.config
from pixel_hawk.config import Config, get_config, load_config


class TestConfig:
    """Tests for Config dataclass."""

    def test_config_computed_properties(self, tmp_path):
        """Test that Config computed properties return correct subdirectories."""
        config = Config(home=tmp_path)

        assert config.projects_dir == tmp_path / "projects"
        assert config.snapshots_dir == tmp_path / "snapshots"
        assert config.metadata_dir == tmp_path / "metadata"
        assert config.tiles_dir == tmp_path / "tiles"
        assert config.logs_dir == tmp_path / "logs"
        assert config.data_dir == tmp_path / "data"

    def test_config_home_is_absolute(self, tmp_path):
        """Test that Config home path can be set."""
        config = Config(home=tmp_path)
        assert config.home == tmp_path
        assert config.home.is_absolute()


class TestLoadConfig:
    """Tests for load_config function."""

    def test_default_pixel_hawk_home(self, monkeypatch):
        """Test that default pixel-hawk-home is ./pixel-hawk-data (converted to absolute)."""
        # Clear environment variable if it exists
        monkeypatch.delenv("PIXEL_HAWK_HOME", raising=False)

        config = load_config(args=[])

        # Should be ./pixel-hawk-data resolved to absolute path
        expected = Path("./pixel-hawk-data").resolve()
        assert config.home == expected

    def test_cli_flag_precedence(self, tmp_path, monkeypatch):
        """Test that --pixel-hawk-home CLI flag takes precedence over env var."""
        cli_path = tmp_path / "cli-home"
        env_path = tmp_path / "env-home"

        monkeypatch.setenv("PIXEL_HAWK_HOME", str(env_path))

        config = load_config(args=["--pixel-hawk-home", str(cli_path)])

        assert config.home == cli_path.resolve()

    def test_env_var_precedence(self, tmp_path, monkeypatch):
        """Test that PIXEL_HAWK_HOME env var takes precedence over default."""
        env_path = tmp_path / "env-home"
        monkeypatch.setenv("PIXEL_HAWK_HOME", str(env_path))

        config = load_config(args=[])

        assert config.home == env_path.resolve()

    def test_cli_flag_with_relative_path(self, tmp_path, monkeypatch):
        """Test that CLI flag converts relative paths to absolute."""
        # Change to tmp_path as working directory
        monkeypatch.chdir(tmp_path)

        config = load_config(args=["--pixel-hawk-home", "my-data"])

        expected = (tmp_path / "my-data").resolve()
        assert config.home == expected
        assert config.home.is_absolute()

    def test_env_var_with_relative_path(self, tmp_path, monkeypatch):
        """Test that env var converts relative paths to absolute."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("PIXEL_HAWK_HOME", "my-env-data")

        config = load_config(args=[])

        expected = (tmp_path / "my-env-data").resolve()
        assert config.home == expected
        assert config.home.is_absolute()

    def test_cli_flag_missing_value(self, monkeypatch):
        """Test that --pixel-hawk-home without value falls back to env or default."""
        monkeypatch.delenv("PIXEL_HAWK_HOME", raising=False)

        # --pixel-hawk-home at end of args with no value
        config = load_config(args=["--pixel-hawk-home"])

        # Should fall back to default since no value provided
        expected = Path("./pixel-hawk-data").resolve()
        assert config.home == expected

    def test_cli_flag_with_other_args(self, tmp_path, monkeypatch):
        """Test that --pixel-hawk-home works correctly with other args present."""
        cli_path = tmp_path / "cli-home"

        config = load_config(args=["--other-flag", "--pixel-hawk-home", str(cli_path), "--another"])

        assert config.home == cli_path.resolve()

    def test_no_args_no_env(self, monkeypatch):
        """Test default behavior when no args or env var."""
        monkeypatch.delenv("PIXEL_HAWK_HOME", raising=False)

        config = load_config(args=[])

        expected = Path("./pixel-hawk-data").resolve()
        assert config.home == expected


class TestGetConfig:
    """Tests for get_config function."""

    def test_get_config_returns_config_when_initialized(self, tmp_path):
        """Test that get_config returns CONFIG when it's set."""
        # Save original CONFIG
        original = pixel_hawk.config.CONFIG
        try:
            # Set CONFIG
            pixel_hawk.config.CONFIG = Config(home=tmp_path)

            config = get_config()
            assert config == pixel_hawk.config.CONFIG
            assert config.home == tmp_path
        finally:
            # Restore original CONFIG
            pixel_hawk.config.CONFIG = original
