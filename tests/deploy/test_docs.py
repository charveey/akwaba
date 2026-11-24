"""Phase 14c — la documentation ne peut pas devenir silencieusement fausse.

Vérifie, pour README.md et docs/*.md :
- chaque lien relatif mène à un fichier existant (et à un titre existant s'il vise une ancre) ;
- chaque script ou fichier du dépôt cité (`deploy/…`, `tests/…`, `backend/…`) existe ;
- chaque option passée à un script du dépôt figure dans ce script ;
- chaque commande `python -m app.cli` cite une sous-commande et des options réelles ;
- le README renvoie à chaque document de docs/.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCS = sorted((ROOT / "docs").glob("*.md"))
FILES = [ROOT / "README.md", *DOCS]

LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
PATH = re.compile(r"(?<![\w./-])((?:\./)?(?:deploy|tests|backend|agent|frontend)/[\w./-]+\.(?:sh|py|md|ini|conf|lock|json|code-workspace))")
SCRIPT_CALL = re.compile(r"(?:\./|sh |sudo (?:sh )?(?:\./)?)?((?:deploy|tests)/[\w./-]+\.sh)((?:[ \t]+[^\s|;&`#]+)*)")
CLI_CALL = re.compile(r"python -m app\.cli ([\w-]+)((?:[ \t]+[^\s|;&`#]+)*)")


def rel(p: Path) -> str:
    return str(p.relative_to(ROOT))


def slug(title: str) -> str:
    """Ancre à la GitHub : minuscules, ponctuation retirée (accents conservés), espaces → tirets."""
    t = title.strip().lower()
    t = "".join(c for c in t if c in " -_" or unicodedata.category(c)[0] in "LN")
    return t.replace(" ", "-")


def anchors(md: Path) -> set[str]:
    out, in_code = set(), False
    for line in md.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            in_code = not in_code
        elif not in_code and (m := re.match(r"#{1,6} (.+)", line)):
            out.add(slug(m.group(1)))
    return out


def test_docs_exist():
    names = {p.name for p in DOCS}
    assert {"architecture.md", "securite.md", "exploitation.md", "historique.md", "developpement.md"} <= names


@pytest.mark.parametrize("md", FILES, ids=rel)
def test_relative_links_resolve(md):
    errors = []
    for target in LINK.findall(md.read_text(encoding="utf-8")):
        if re.match(r"[a-z]+:", target):
            continue
        path, _, anchor = target.partition("#")
        dest = (md.parent / path).resolve() if path else md
        if not dest.exists():
            errors.append(f"{target} : fichier absent")
        elif anchor and dest.suffix == ".md" and anchor not in anchors(dest):
            errors.append(f"{target} : ancre absente")
    assert not errors, f"{rel(md)} : {errors}"


@pytest.mark.parametrize("md", FILES, ids=rel)
def test_cited_repo_files_exist(md):
    text = md.read_text(encoding="utf-8")
    missing = sorted({p for p in PATH.findall(text) if "<" not in p and not (ROOT / p.removeprefix("./")).exists()})
    assert not missing, f"{rel(md)} cite des fichiers absents : {missing}"


@pytest.mark.parametrize("md", FILES, ids=rel)
def test_script_options_exist(md):
    errors = []
    for script, args in SCRIPT_CALL.findall(md.read_text(encoding="utf-8")):
        source = ROOT / script
        if not source.exists():
            continue  # signalé par test_cited_repo_files_exist
        body = source.read_text(encoding="utf-8")
        for opt in re.findall(r"(?<!\S)(--[a-z][\w-]*)", args):
            if not re.search(rf"(?<![\w-]){re.escape(opt)}(?![\w-])", body):
                errors.append(f"{script} {opt}")
    assert not errors, f"{rel(md)} : options inconnues {errors}"


@pytest.mark.parametrize("md", FILES, ids=rel)
def test_cli_commands_exist(md):
    cli = (ROOT / "backend/app/cli.py").read_text(encoding="utf-8")
    errors = []
    for sub, args in CLI_CALL.findall(md.read_text(encoding="utf-8")):
        if f'add_parser("{sub}"' not in cli:
            errors.append(sub)
            continue
        for opt in re.findall(r"(?<!\S)(--[a-z][\w-]*)", args):
            if f'add_argument("{opt}"' not in cli:
                errors.append(f"{sub} {opt}")
    assert not errors, f"{rel(md)} : commandes CLI inconnues {errors}"


def test_readme_links_every_doc():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    missing = [p.name for p in DOCS if f"(docs/{p.name})" not in readme]
    assert not missing, f"README.md ne renvoie pas à : {missing}"


def test_readme_stays_short():
    assert len((ROOT / "README.md").read_text(encoding="utf-8").splitlines()) <= 80


def test_dry_run_principle_documented():
    for md in (ROOT / "README.md", ROOT / "docs/architecture.md"):
        text = md.read_text(encoding="utf-8")
        assert "REVOCATION_MODE=dry_run" in text and "ENFORCER_ENABLED=false" in text, rel(md)


# ------------------------------------------------------------------ le test lui-même

def test_checker_catches_bad_option(tmp_path):
    fake = "./deploy/restore.sh x.dump --option-inexistante"
    script, args = SCRIPT_CALL.findall(fake)[0]
    body = (ROOT / script).read_text(encoding="utf-8")
    assert "--option-inexistante" in args and "--option-inexistante" not in body


def test_slug_matches_github_for_french_titles():
    assert slug("Déployer une nouvelle version") == "déployer-une-nouvelle-version"
    assert slug("Mise en place (une fois)") == "mise-en-place-une-fois"
    assert slug("`deploy.sh` refuse : variante Debian") == "deploysh-refuse--variante-debian"
