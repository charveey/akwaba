"""Lot 11a — parcours navigateur réel (connexion, session, CSP, thème, mobile). Pile : conftest.py."""
from conftest import SKIP, TAILNET_CLIENT_IP, run_browser

pytestmark = SKIP


def test_browser_journey(stack):
    run_browser(stack, "browser_11a.mjs")


def test_audit_records_real_tailnet_ip(stack):
    """L'IP ajoutée par tailscaled traverse Nginx (realip) et le backend (TRUSTED_PROXY_IPS)."""
    from sqlalchemy import text
    with stack["engine"].connect() as c:
        ips = c.execute(text(
            "SELECT DISTINCT ip_address FROM audit_logs WHERE action LIKE 'admin.login%'"
        )).scalars().all()
    assert ips == [TAILNET_CLIENT_IP], ips


def test_nginx_access_log_has_no_secrets(stack):
    log = (stack["tmp"] / "access.log").read_text()
    assert "akwaba_session" not in log and stack["password"] not in log
    assert TAILNET_CLIENT_IP in log
