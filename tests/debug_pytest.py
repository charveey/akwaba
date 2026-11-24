"""AKWABA — lanceur de tests pour le DÉBOGUEUR de l'éditeur (Zed : Debugpy ; VS Code : debugpy).

    python tests/debug_pytest.py FICHIER [-k EXPR]

Même aiguillage que tests/run.sh, mais pytest tourne DANS ce processus (pytest.main) : les points d'arrêt
fonctionnent dans les tests ET dans le code testé (app/…, akwaba_agent/…). Charge .env.test (jamais .env).
Les tests de bout en bout démarrent le backend dans un AUTRE processus : y déboguer le code applicatif
passe par les tests backend (même code, même base de test).
"""
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_env_test() -> None:
    path = ROOT / ".env.test"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def route(target: str, kexpr: str = ""):
    """(répertoire de travail, arguments pytest) pour un fichier ou un dossier du dépôt."""
    path = pathlib.Path(target)
    path = (path if path.is_absolute() else pathlib.Path.cwd() / path).resolve()
    try:
        rel = path.relative_to(ROOT)
    except ValueError:
        raise SystemExit(f"hors du dépôt : {path}")
    if not path.exists():
        raise SystemExit(f"introuvable : {path}")
    k = ["-k", kexpr.split(" > ")[-1]] if kexpr.strip() else []
    parts = rel.parts
    is_test = path.is_dir() or path.name.startswith("test_") or path.name == "conftest.py"
    if parts and parts[0] in ("backend", "agent"):
        comp = ROOT / parts[0]
        inner = pathlib.Path(*parts[1:]) if len(parts) > 1 else None
        if is_test and inner is not None and inner.parts and inner.parts[0] == "tests":
            return comp, [str(inner), *k]
        return comp, k
    if parts and parts[0] == "tests" and path.suffix == ".py":
        if is_test:
            return ROOT, ["-q", "-p", "no:cacheprovider", str(rel), *k]
    if parts and parts[0] == "tests" and path.suffix == ".mjs":
        drivers = [p for p in sorted((ROOT / "tests" / "e2e").glob("test_*.py")) if path.name in p.read_text()]
        if drivers:
            return ROOT, ["-q", "-p", "no:cacheprovider", str(drivers[0].relative_to(ROOT)), *k]
    raise SystemExit(f"pas de test Python à déboguer pour {rel} (frontend : configuration « Node » de l'éditeur)")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    kexpr = ""
    if "-k" in argv:
        i = argv.index("-k")
        kexpr = argv[i + 1] if i + 1 < len(argv) else ""
        del argv[i:i + 2]
    if not argv:
        raise SystemExit("usage : debug_pytest.py FICHIER [-k EXPR]")
    cwd, args = route(argv[0], kexpr)
    load_env_test()
    os.chdir(cwd)
    if str(cwd) not in sys.path:
        sys.path.insert(0, str(cwd))
    if os.environ.get("AKWABA_DEBUG_PLAN"):
        print("cd %s && pytest %s" % (os.path.relpath(cwd, ROOT), " ".join(args)))
        return 0
    import pytest
    return pytest.main(args)


if __name__ == "__main__":
    sys.exit(main())
