"""Configuration de l'agent (INI, bibliothèque standard). Aucun secret dans ce fichier."""
from __future__ import annotations

import configparser
import ipaddress
import os
from dataclasses import dataclass
from typing import Optional, Tuple

from akwaba_agent.collector.timeouts import DEFAULT_DIR


class ConfigError(Exception):
    pass


@dataclass
class Config:
    window_seconds: int = 60
    interval_seconds: int = 60
    use_sudo: bool = False
    quiet: bool = False
    exclude_sources: Tuple[str, ...] = ()
    spool_path: Optional[str] = None
    heartbeat_path: Optional[str] = None
    timeouts_dir: str = DEFAULT_DIR
    min_replied_flows: int = 3
    required_windows: int = 2
    saturation_threshold: float = 0.8


_KEYS = {
    "window_seconds", "interval_seconds", "use_sudo", "quiet", "exclude_sources", "spool_path",
    "heartbeat_path", "timeouts_dir", "min_replied_flows", "required_windows", "saturation_threshold",
}
_BOOLS = {"yes": True, "true": True, "1": True, "on": True, "no": False, "false": False, "0": False, "off": False}


def _int(raw: dict, key: str, default: int) -> int:
    if key not in raw:
        return default
    try:
        return int(raw[key])
    except ValueError:
        raise ConfigError("%s doit être un entier" % key)


def _bool(raw: dict, key: str, default: bool) -> bool:
    if key not in raw:
        return default
    value = _BOOLS.get(raw[key].strip().lower())
    if value is None:
        raise ConfigError("%s doit être yes ou no" % key)
    return value


def load_config(path: str, must_exist: bool = True) -> Config:
    if not os.path.exists(path):
        if must_exist:
            raise ConfigError("fichier de configuration introuvable")
        return Config()
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with open(path, encoding="utf-8") as fh:
            parser.read_file(fh)
    except (OSError, configparser.Error, UnicodeDecodeError):
        raise ConfigError("fichier de configuration illisible")
    if set(parser.sections()) - {"agent"}:
        raise ConfigError("section inconnue (seule [agent] existe)")
    raw = dict(parser["agent"]) if parser.has_section("agent") else {}
    unknown = set(raw) - _KEYS
    if unknown:
        raise ConfigError("clé inconnue : %s" % ", ".join(sorted(unknown)))

    window = _int(raw, "window_seconds", 60)
    interval = _int(raw, "interval_seconds", 60)
    min_replied = _int(raw, "min_replied_flows", 3)
    windows = _int(raw, "required_windows", 2)
    try:
        threshold = float(raw.get("saturation_threshold", "0.8"))
    except ValueError:
        raise ConfigError("saturation_threshold doit être un nombre")
    if window <= 0 or interval <= 0 or interval > window:
        raise ConfigError("il faut 0 < interval_seconds <= window_seconds")
    if min_replied < 1 or windows < 1:
        raise ConfigError("min_replied_flows et required_windows doivent être >= 1")
    if not 0 < threshold <= 1:
        raise ConfigError("saturation_threshold doit être dans ]0, 1]")
    try:
        excluded = tuple(str(ipaddress.ip_address(x)) for x in raw.get("exclude_sources", "").replace(",", " ").split())
    except ValueError:
        raise ConfigError("exclude_sources contient une adresse IP invalide")

    spool = raw.get("spool_path", "").strip() or None
    heartbeat = raw.get("heartbeat_path", "").strip() or None
    if heartbeat is None and spool:
        heartbeat = os.path.join(os.path.dirname(spool), "heartbeat.json")
    return Config(
        window_seconds=window, interval_seconds=interval,
        use_sudo=_bool(raw, "use_sudo", False), quiet=_bool(raw, "quiet", False),
        exclude_sources=excluded, spool_path=spool, heartbeat_path=heartbeat,
        timeouts_dir=raw.get("timeouts_dir", "").strip() or DEFAULT_DIR,
        min_replied_flows=min_replied, required_windows=windows, saturation_threshold=threshold,
    )
