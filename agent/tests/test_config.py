import pytest

from akwaba_agent.config import Config, ConfigError, load_config


def write(tmp_path, text):
    p = tmp_path / "agent.ini"
    p.write_text(text)
    return str(p)


def test_defaults():
    c = Config()
    assert (c.window_seconds, c.interval_seconds, c.min_replied_flows, c.required_windows) == (60, 60, 3, 2)
    assert c.exclude_sources == () and c.spool_path is None and c.saturation_threshold == 0.8


def test_file_values_and_derived_heartbeat_path(tmp_path):
    c = load_config(write(tmp_path, (
        "[agent]\nwindow_seconds = 90\ninterval_seconds = 30\nuse_sudo = yes\nquiet = no\n"
        "exclude_sources = 100.85.214.5, fd7a:115c:a1e0::1\nspool_path = /var/lib/x/agent.db\n"
        "saturation_threshold = 0.7\n")))
    assert (c.window_seconds, c.interval_seconds, c.use_sudo, c.quiet) == (90, 30, True, False)
    assert c.exclude_sources == ("100.85.214.5", "fd7a:115c:a1e0::1")
    assert c.spool_path == "/var/lib/x/agent.db" and c.heartbeat_path == "/var/lib/x/heartbeat.json"
    assert c.saturation_threshold == 0.7


def test_missing_agent_section_gives_defaults(tmp_path):
    assert load_config(write(tmp_path, "")).window_seconds == 60


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError):
        load_config(str(tmp_path / "absent.ini"), must_exist=True)
    assert load_config(str(tmp_path / "absent.ini"), must_exist=False).window_seconds == 60


@pytest.mark.parametrize("body", [
    "[agent]\nwindow_seconds = 0\n",
    "[agent]\nwindow_seconds = 30\n",                     # l'intervalle par défaut (60) dépasse la fenêtre
    "[agent]\nwindow_seconds = abc\n",
    "[agent]\nexclude_sources = pas-une-ip\n",
    "[agent]\nuse_sudo = peutetre\n",
    "[agent]\nmin_replied_flows = 0\n",
    "[agent]\nsaturation_threshold = 1.5\n",
    "[agent]\nsaturation_threshold = abc\n",
    "[agent]\nwindw_seconds = 60\n",                      # faute de frappe : clé inconnue
    "[autre]\nx = 1\n",
    "pas un fichier ini",
])
def test_invalid_configurations_are_refused(tmp_path, body):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, body))
