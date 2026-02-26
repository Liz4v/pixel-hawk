"""Tests for configuration management."""

import tomllib
from pathlib import Path

import pixel_hawk.models.config
from pixel_hawk.models.config import Config, DiscordSettings, generate_default_config, get_config, load_config


class TestConfig:
    """Tests for Config dataclass."""

    def test_config_computed_properties(self, tmp_path):
        """Test that Config computed properties return correct subdirectories."""
        config = Config(home=tmp_path)

        assert config.projects_dir == tmp_path / "projects"
        assert config.snapshots_dir == tmp_path / "snapshots"
        assert config.tiles_dir == tmp_path / "tiles"
        assert config.logs_dir == tmp_path / "logs"
        assert config.data_dir == tmp_path / "data"

    def test_config_home_is_absolute(self, tmp_path):
        """Test that Config home path can be set."""
        config = Config(home=tmp_path)
        assert config.home == tmp_path
        assert config.home.is_absolute()

    def test_config_toml_missing_file(self, tmp_path):
        """Test that config_toml returns empty dict when file doesn't exist."""
        config = Config(home=tmp_path)
        assert config.config_toml == {}

    def test_config_toml_valid_file(self, tmp_path):
        """Test that config_toml reads and parses TOML file."""
        config = Config(home=tmp_path)
        (tmp_path / "config.toml").write_text('[discord]\nbot_token = "abc"\n')
        assert config.config_toml == {"discord": {"bot_token": "abc"}}

    def test_config_toml_cached(self, tmp_path):
        """Test that config_toml is cached across accesses."""
        config = Config(home=tmp_path)
        (tmp_path / "config.toml").write_text('[discord]\nbot_token = "first"\n')
        first = config.config_toml
        (tmp_path / "config.toml").write_text('[discord]\nbot_token = "second"\n')
        second = config.config_toml
        assert first is second  # Same object, cached


class TestDiscordSettings:
    """Tests for Config.discord typed settings."""

    def test_defaults_without_config_file(self, tmp_path):
        config = Config(home=tmp_path)
        assert config.discord == DiscordSettings(bot_token="", command_prefix="hawk")

    def test_reads_bot_token(self, tmp_path):
        (tmp_path / "config.toml").write_text('[discord]\nbot_token = "secret"\n')
        config = Config(home=tmp_path)
        assert config.discord.bot_token == "secret"

    def test_empty_bot_token_stays_empty(self, tmp_path):
        (tmp_path / "config.toml").write_text('[discord]\nbot_token = ""\n')
        config = Config(home=tmp_path)
        assert config.discord.bot_token == ""

    def test_reads_command_prefix(self, tmp_path):
        (tmp_path / "config.toml").write_text('[discord]\ncommand_prefix = "testhawk"\n')
        config = Config(home=tmp_path)
        assert config.discord.command_prefix == "testhawk"

    def test_command_prefix_defaults_to_hawk(self, tmp_path):
        (tmp_path / "config.toml").write_text("[discord]\n")
        config = Config(home=tmp_path)
        assert config.discord.command_prefix == "hawk"

    def test_discord_cached(self, tmp_path):
        (tmp_path / "config.toml").write_text('[discord]\nbot_token = "x"\n')
        config = Config(home=tmp_path)
        assert config.discord is config.discord


class TestGenerateDefaultConfig:
    """Tests for generate_default_config function."""

    def test_output_is_valid_toml(self):
        result = generate_default_config()
        parsed = tomllib.loads(result)
        assert "discord" in parsed

    def test_includes_discord_defaults(self):
        parsed = tomllib.loads(generate_default_config())
        assert parsed["discord"]["bot_token"] == ""
        assert parsed["discord"]["command_prefix"] == "hawk"

    def test_includes_docstring_as_comment(self):
        result = generate_default_config()
        assert "# Typed settings from the [discord] section of config.toml." in result

    def test_roundtrips_through_discord_settings(self):
        """Generated TOML should produce the same DiscordSettings as bare defaults."""
        config = generate_default_config()
        parsed = tomllib.loads(config)
        settings = DiscordSettings(**parsed["discord"])
        assert settings == DiscordSettings()


class TestLoadConfigGeneratesConfig:
    """Tests for auto-generation of config.toml in load_config."""

    def test_creates_config_toml_when_missing(self, tmp_path, monkeypatch):
        monkeypatch.delenv("HAWK_NEST", raising=False)
        load_config(args=["--nest", str(tmp_path)])
        assert (tmp_path / "config.toml").exists()

    def test_does_not_overwrite_existing_config(self, tmp_path, monkeypatch):
        monkeypatch.delenv("HAWK_NEST", raising=False)
        (tmp_path / "config.toml").write_text('[discord]\nbot_token = "keep"\n')
        load_config(args=["--nest", str(tmp_path)])
        parsed = tomllib.loads((tmp_path / "config.toml").read_text())
        assert parsed["discord"]["bot_token"] == "keep"


class TestLoadConfig:
    """Tests for load_config function."""

    def test_default_hawk_nest(self, monkeypatch):
        """Test that default nest is ./nest (converted to absolute)."""
        # Clear environment variable if it exists
        monkeypatch.delenv("HAWK_NEST", raising=False)

        config = load_config(args=[])

        # Should be ./nest resolved to absolute path
        expected = Path("./nest").resolve()
        assert config.home == expected

    def test_cli_flag_precedence(self, tmp_path, monkeypatch):
        """Test that --nest CLI flag takes precedence over env var."""
        cli_path = tmp_path / "cli-home"
        env_path = tmp_path / "env-home"

        monkeypatch.setenv("HAWK_NEST", str(env_path))

        config = load_config(args=["--nest", str(cli_path)])

        assert config.home == cli_path.resolve()

    def test_env_var_precedence(self, tmp_path, monkeypatch):
        """Test that HAWK_NEST env var takes precedence over default."""
        env_path = tmp_path / "env-home"
        monkeypatch.setenv("HAWK_NEST", str(env_path))

        config = load_config(args=[])

        assert config.home == env_path.resolve()

    def test_cli_flag_with_relative_path(self, tmp_path, monkeypatch):
        """Test that CLI flag converts relative paths to absolute."""
        # Change to tmp_path as working directory
        monkeypatch.chdir(tmp_path)

        config = load_config(args=["--nest", "my-data"])

        expected = (tmp_path / "my-data").resolve()
        assert config.home == expected
        assert config.home.is_absolute()

    def test_env_var_with_relative_path(self, tmp_path, monkeypatch):
        """Test that env var converts relative paths to absolute."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("HAWK_NEST", "my-env-data")

        config = load_config(args=[])

        expected = (tmp_path / "my-env-data").resolve()
        assert config.home == expected
        assert config.home.is_absolute()

    def test_cli_flag_missing_value(self, monkeypatch):
        """Test that --nest without value falls back to env or default."""
        monkeypatch.delenv("HAWK_NEST", raising=False)

        # --nest at end of args with no value
        config = load_config(args=["--nest"])

        # Should fall back to default since no value provided
        expected = Path("./nest").resolve()
        assert config.home == expected

    def test_cli_flag_with_other_args(self, tmp_path, monkeypatch):
        """Test that --nest works correctly with other args present."""
        cli_path = tmp_path / "cli-home"

        config = load_config(args=["--other-flag", "--nest", str(cli_path), "--another"])

        assert config.home == cli_path.resolve()

    def test_no_args_no_env(self, monkeypatch):
        """Test default behavior when no args or env var."""
        monkeypatch.delenv("HAWK_NEST", raising=False)

        config = load_config(args=[])

        expected = Path("./nest").resolve()
        assert config.home == expected


class TestGetConfig:
    """Tests for get_config function."""

    def test_get_config_returns_config_when_initialized(self, tmp_path):
        """Test that get_config returns CONFIG when it's set."""
        # Save original CONFIG
        original = pixel_hawk.models.config.CONFIG
        try:
            # Set CONFIG
            pixel_hawk.models.config.CONFIG = Config(home=tmp_path)

            config = get_config()
            assert config == pixel_hawk.models.config.CONFIG
            assert config.home == tmp_path
        finally:
            # Restore original CONFIG
            pixel_hawk.models.config.CONFIG = original
