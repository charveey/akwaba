"""Outillage des éditeurs (Zed, VS Code) : tests/run.sh, tests/test-db.sh, fichiers de configuration."""
import json
import os
import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
RUN = ROOT / "tests" / "run.sh"


def plan(*args, cwd=ROOT, env=None):
    res = subprocess.run([str(RUN), "--plan", *args], cwd=cwd, capture_output=True, text=True,
                         env={**os.environ, "AKWABA_PYTHON": "python3", **(env or {})}, timeout=30)
    return res.returncode, res.stdout.splitlines()[0] if res.stdout else res.stderr.strip()


@pytest.mark.parametrize("args,expected", [
    ([], "cd . && tests/run-all.sh"),
    (["backend/tests/test_csv_and_mask.py"], "cd backend && python3 -m pytest tests/test_csv_and_mask.py"),
    (["backend/tests/test_csv_and_mask.py", "-k", "test_neutralize"],
     "cd backend && python3 -m pytest tests/test_csv_and_mask.py -k test_neutralize"),
    (["backend/app/main.py"], "cd backend && python3 -m pytest"),
    (["agent/tests"], "cd agent && python3 -m pytest tests"),
    (["frontend/tests/lot11d.test.mjs", "-k", "axe"],
     "cd frontend && node --test --test-name-pattern axe tests/lot11d.test.mjs"),
    (["frontend/assets/js/app.js"], "cd frontend && node --test tests/"),
    (["tests/e2e/browser_11c.mjs"], "cd . && python3 -m pytest -q -p no:cacheprovider tests/e2e/test_dashboard_11c.py"),
    (["tests/deploy/test_images_13a.py"], "cd . && python3 -m pytest -q -p no:cacheprovider tests/deploy/test_images_13a.py"),
    (["deploy/deploy.sh"], "cd . && python3 -m pytest -q -p no:cacheprovider tests/deploy"),
    (["README.md"], "cd . && tests/run-all.sh"),
])
def test_run_routes_each_file_to_the_right_suite(args, expected):
    rc, first = plan(*args)
    assert rc == 0 and first == expected, first


def test_zed_symbol_breadcrumb_and_empty_symbol():
    assert plan("backend/tests/test_csv_and_mask.py", "-k", "TestX > test_y")[1].endswith("-k test_y")
    assert plan("backend/tests/test_csv_and_mask.py", "-k", "")[1] == \
        "cd backend && python3 -m pytest tests/test_csv_and_mask.py"


def test_absolute_paths_and_other_working_directories():
    abs_path = str(ROOT / "agent" / "tests")
    assert plan(abs_path)[1] == "cd agent && python3 -m pytest tests"
    assert plan("tests/lot11d.test.mjs", cwd=ROOT / "frontend")[1] == "cd frontend && node --test tests/lot11d.test.mjs"


def test_refuses_missing_files_and_paths_outside_the_repository(tmp_path):
    assert plan("backend/tests/nexiste_pas.py")[0] == 2
    outside = tmp_path / "x.py"
    outside.write_text("")
    rc, msg = plan(str(outside))
    assert rc == 2 and "hors du dépôt" in msg


def test_env_test_is_loaded_never_env(tmp_path):
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "backend" / "tests").mkdir(parents=True)
    shutil.copy(RUN, repo / "tests" / "run.sh")
    (repo / "backend" / "tests" / "test_x.py").write_text("")
    (repo / ".env").write_text("TEST_DATABASE_URL=production-interdite\n")
    res = subprocess.run([str(repo / "tests" / "run.sh"), "--plan", "backend/tests/test_x.py"], cwd=repo,
                         capture_output=True, text=True, env={k: v for k, v in os.environ.items()
                                                              if k != "TEST_DATABASE_URL"})
    assert "TEST_DATABASE_URL" not in res.stdout, ".env (production) n'est jamais chargé"
    (repo / ".env.test").write_text("TEST_DATABASE_URL=postgresql+psycopg://akwaba:akwaba@127.0.0.1:5433/akwaba_test\n")
    res = subprocess.run([str(repo / "tests" / "run.sh"), "--plan", "backend/tests/test_x.py"], cwd=repo,
                         capture_output=True, text=True)
    assert "TEST_DATABASE_URL défini par .env.test" in res.stdout


def test_venv_python_is_preferred(tmp_path):
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "agent" / "tests").mkdir(parents=True)
    (repo / ".venv" / "bin").mkdir(parents=True)
    (repo / ".venv" / "bin" / "python").write_text("#!/bin/sh\n")
    (repo / ".venv" / "bin" / "python").chmod(0o755)
    shutil.copy(RUN, repo / "tests" / "run.sh")
    env = {k: v for k, v in os.environ.items() if k != "AKWABA_PYTHON"}
    res = subprocess.run([str(repo / "tests" / "run.sh"), "--plan", "agent/tests"], cwd=repo, capture_output=True,
                         text=True, env=env)
    assert res.stdout.startswith("cd agent && .venv/bin/python -m pytest tests"), res.stdout


def test_test_database_is_local_disposable_and_uses_the_production_image():
    res = subprocess.run([str(ROOT / "tests" / "test-db.sh"), "--plan", "up"], capture_output=True, text=True,
                         cwd=ROOT, env={**os.environ, "AKWABA_TEST_DB_PORT": "5433"})
    run_line = res.stdout.splitlines()[0]
    compose_image = re.search(r"^\s*image:\s*(postgres:\S+)", (ROOT / "docker-compose.yml").read_text(), re.M).group(1)
    assert run_line.endswith(compose_image)
    assert "-p 127.0.0.1:5433:5432" in run_line and "--tmpfs /var/lib/postgresql/data" in run_line
    assert " -v " not in run_line and "--name akwaba-test-db" in run_line
    assert not (ROOT / ".env.test").exists() or "--plan" in res.args


def _tasks():
    vscode = json.loads((ROOT / ".vscode" / "tasks.json").read_text())["tasks"]
    zed = json.loads((ROOT / ".zed" / "tasks.json").read_text())
    return vscode, zed


def test_every_editor_task_calls_an_existing_executable_script():
    vscode, zed = _tasks()
    for t in vscode:
        script = ROOT / t["command"].replace("${workspaceFolder}/", "")
        assert script.exists() and os.access(script, os.X_OK), t["label"]
    for t in zed:
        script = ROOT / t["command"]
        assert script.exists() and os.access(script, os.X_OK), t["label"]
        assert t.get("cwd") == "$ZED_WORKTREE_ROOT", t["label"]
    assert any(t.get("group", {}).get("isDefault") for t in vscode if isinstance(t.get("group"), dict))


def test_zed_gutter_buttons_are_bound_to_run_sh():
    _, zed = _tasks()
    tags = {tag: t for t in zed for tag in t.get("tags", [])}
    assert {"python-pytest-method", "python-pytest-class", "js-test"} <= set(tags)
    assert tags["python-pytest-method"]["args"] == ["$ZED_FILE", "-k", "${ZED_SYMBOL:}"]


def test_vscode_discovers_each_python_suite_in_its_own_folder():
    ws = json.loads((ROOT / "akwaba.code-workspace").read_text())
    assert [f["path"] for f in ws["folders"]] == ["backend", "agent", "frontend", "."]
    for comp in ("backend", "agent"):
        s = json.loads((ROOT / comp / ".vscode" / "settings.json").read_text())
        assert s["python.testing.pytestArgs"] == ["tests"] and s["python.testing.cwd"] == "${workspaceFolder}"
        assert s["python.envFile"] == "${workspaceFolder}/../.env.test"
    root = json.loads((ROOT / ".vscode" / "settings.json").read_text())
    assert root["python.testing.pytestArgs"][-3:] == ["tests/nginx", "tests/deploy", "tests/e2e"]


def test_shared_editor_files_are_tracked_and_env_test_is_ignored():
    def ignored(path):
        return subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT).returncode == 0
    for path in (".vscode/settings.json", ".vscode/tasks.json", ".vscode/extensions.json", ".zed/tasks.json",
                 "backend/.vscode/settings.json", "agent/.vscode/settings.json", "akwaba.code-workspace"):
        assert not ignored(path), path
    for path in (".env.test", ".env", ".vscode/launch.json"):
        assert ignored(path), path
