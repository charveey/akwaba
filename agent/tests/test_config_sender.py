import pytest

from akwaba_agent.config import ConfigError, load_config

BLOCK = "backend_url = http://100.126.200.88:8000\nagent_id = agt_dacd18597874a0aa\nsecret_path = /etc/akwaba-agent/secret\n"


def write(tmp_path, body):
    p = tmp_path / "agent.ini"
    p.write_text("[agent]\n" + body)
    return str(p)


def test_sender_settings_are_parsed(tmp_path):
    c = load_config(write(tmp_path, BLOCK + "request_timeout_seconds = 15\n"))
    assert (c.backend_url, c.agent_id, c.secret_path, c.request_timeout_seconds) == (
        "http://100.126.200.88:8000", "agt_dacd18597874a0aa", "/etc/akwaba-agent/secret", 15)


def test_sender_is_optional_and_defaults_are_off(tmp_path):
    c = load_config(write(tmp_path, ""))
    assert (c.backend_url, c.agent_id, c.secret_path, c.request_timeout_seconds) == (None, None, None, 10)


@pytest.mark.parametrize("body", [
    "backend_url = http://100.126.200.88:8000\n",
    "agent_id = agt_x\nsecret_path = /s\n",
    BLOCK.replace("http://", "ftp://"),
    BLOCK.replace(":8000", ":8000/api"),
    BLOCK.replace("agt_dacd18597874a0aa", "agt avec espace"),
    BLOCK + "request_timeout_seconds = 0\n",
    BLOCK + "request_timeout_seconds = 61\n",
    BLOCK + "request_timeout_seconds = abc\n",
])
def test_invalid_sender_settings_are_refused(tmp_path, body):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, body))
