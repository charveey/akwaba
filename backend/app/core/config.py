"""Configuration de l'application, validée au démarrage (fail-fast)."""
import ipaddress
import re
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Clé Fernet : base64 URL-safe de 32 octets = 43 caractères + "=".
_FERNET_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{43}=$")
# Origine du navigateur : https://hôte[:port], rien d'autre.
_ORIGIN_RE = re.compile(r"^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?$")


class Settings(BaseSettings):
    # hide_input_in_errors : une valeur invalide (ex. SECRET_KEY) n'est jamais
    # recopiée dans les messages d'erreur ni dans les logs.
    model_config = SettingsConfigDict(extra="ignore", hide_input_in_errors=True)

    app_name: str = "AKWABA VPN MANAGEMENT"
    app_version: str = "0.1.0"
    log_level: str = "INFO"
    enable_docs: bool = False

    database_url: str
    secret_key: str
    field_encryption_key: str

    tailscale_api_key: str = ""
    tailscale_tailnet: str = ""

    # V1 : observation uniquement. Aucune autre valeur n'est acceptée.
    revocation_mode: Literal["dry_run"] = "dry_run"
    enforcer_enabled: bool = False

    unknown_user_grace_period_hours: int = Field(default=24, ge=1, le=720)
    default_grace_period_days: int = Field(default=3, ge=0, le=30)
    renewal_in_grace_start: Literal["PREVIOUS_EXPIRY", "PAYMENT_DATE"] = "PREVIOUS_EXPIRY"
    default_currency: str = Field(default="XOF", pattern=r"^[A-Z]{3}$")

    cookie_secure: bool = True
    session_idle_hours: int = Field(default=12, ge=1, le=168)
    session_absolute_days: int = Field(default=7, ge=1, le=90)
    login_max_failures: int = Field(default=5, ge=3, le=20)
    login_lockout_minutes: int = Field(default=15, ge=1, le=1440)

    reconciler_enabled: bool = False
    reconciler_interval_seconds: int = Field(default=60, ge=10, le=3600)

    purge_enabled: bool = False
    purge_interval_seconds: int = Field(default=3600, ge=300, le=86400)
    purge_batch_size: int = Field(default=5000, ge=100, le=50000)
    retention_heartbeats_days: int = Field(default=30, ge=7, le=3650)
    retention_job_runs_days: int = Field(default=30, ge=7, le=3650)
    retention_observations_days: int = Field(default=90, ge=14, le=3650)
    retention_sessions_days: int = Field(default=365, ge=14, le=3650)

    # IP ou CIDR séparés par des virgules (ex. "100.85.214.5").
    agent_allowed_ips: str = ""

    # Origines HTTPS autorisées pour les requêtes administratives mutatives
    # (ex. "https://ubuntu.tail14614.ts.net:8443"). Obligatoire : sans elle,
    # aucune requête mutative ne pourrait être acceptée.
    allowed_origins: str

    # IP exactes (pas de CIDR) dont on accepte X-Real-IP : uniquement Nginx.
    # Vide = aucun proxy de confiance, l'IP TCP est toujours utilisée.
    trusted_proxy_ips: str = ""

    @field_validator("database_url")
    @classmethod
    def _check_database_url(cls, v: str) -> str:
        if not v.startswith("postgresql+psycopg://"):
            raise ValueError("DATABASE_URL doit commencer par postgresql+psycopg://")
        return v

    @field_validator("secret_key")
    @classmethod
    def _check_secret_key(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("SECRET_KEY doit contenir au moins 32 caractères")
        return v

    @field_validator("field_encryption_key")
    @classmethod
    def _check_encryption_key(cls, v: str) -> str:
        if not _FERNET_KEY_RE.match(v):
            raise ValueError(
                "FIELD_ENCRYPTION_KEY doit être du base64 URL-safe de 32 octets "
                "(générer avec: openssl rand -base64 32 | tr '+/' '-_')"
            )
        return v

    @field_validator("enforcer_enabled")
    @classmethod
    def _forbid_enforcer(cls, v: bool) -> bool:
        if v:
            raise ValueError("ENFORCER_ENABLED=true est interdit en V1 (dry-run uniquement)")
        return v

    @field_validator("agent_allowed_ips")
    @classmethod
    def _check_agent_ips(cls, v: str) -> str:
        for raw in v.split(","):
            raw = raw.strip()
            if raw:
                ipaddress.ip_network(raw, strict=False)  # ValueError si invalide
        return v

    @field_validator("allowed_origins")
    @classmethod
    def _check_allowed_origins(cls, v: str) -> str:
        items = [x.strip() for x in v.split(",") if x.strip()]
        if not items:
            raise ValueError("ALLOWED_ORIGINS doit contenir au moins une origine HTTPS")
        for item in items:
            if not _ORIGIN_RE.match(item):
                raise ValueError(
                    "ALLOWED_ORIGINS : chaque origine doit être de la forme https://hôte[:port], "
                    "sans chemin ni barre finale"
                )
        return ",".join(items)

    @field_validator("trusted_proxy_ips")
    @classmethod
    def _check_trusted_proxies(cls, v: str) -> str:
        for raw in v.split(","):
            raw = raw.strip()
            if raw:
                ipaddress.ip_address(raw)  # ValueError si invalide ou si c'est un réseau
        return v

    @property
    def allowed_origin_list(self) -> list[str]:
        return [x.strip() for x in self.allowed_origins.split(",") if x.strip()]

    @property
    def trusted_proxy_ip_set(self) -> frozenset[str]:
        return frozenset(
            str(ipaddress.ip_address(x.strip())) for x in self.trusted_proxy_ips.split(",") if x.strip()
        )

    @property
    def agent_allowed_ip_list(self) -> list[str]:
        return [x.strip() for x in self.agent_allowed_ips.split(",") if x.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
