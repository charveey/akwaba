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
        script = ROOT / t["command"].replace("${workspaceFolder}/", "").split(" ")[0]
        assert script.exists() and os.access(script, os.X_OK), t["label"]
    for t in zed:
        # Chemin ABSOLU : Zed lance `bash -i -c`, et un ~/.bashrc qui change de répertoire casserait un chemin relatif.
        cmd = t["command"].lstrip('"')
        assert cmd.startswith("$ZED_WORKTREE_ROOT/"), t["label"]
        script = ROOT / cmd.replace("$ZED_WORKTREE_ROOT/", "").split('"')[0].split(" ")[0]
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
    for path in (".vscode/settings.json", ".vscode/tasks.json", ".vscode/extensions.json", ".vscode/launch.json",
                 ".zed/tasks.json", ".zed/debug.json", "backend/.coveragerc", "agent/.coveragerc",
                 "backend/.vscode/settings.json", "agent/.vscode/settings.json", "akwaba.code-workspace"):
        assert not ignored(path), path
    for path in (".env.test", ".env", "backend/couverture/lcov.info", "frontend/couverture/lcov.info",
                 "backend/couverture/.coverage", ".coverage"):
        assert ignored(path), path


def test_zed_tasks_survive_a_bashrc_that_changes_directory(tmp_path):
    """Reproduit l'appel de Zed (`bash -i -c`) avec un ~/.bashrc qui fait `cd` ailleurs."""
    (tmp_path / ".bashrc").write_text('cd "$HOME"\n')
    _, zed = _tasks()
    task = next(t for t in zed if "python-pytest-method" in t.get("tags", []))
    cmd = task["command"] + ' --plan "$ZED_FILE" -k test_x'
    res = subprocess.run(["/bin/bash", "-i", "-c", cmd], cwd=ROOT, capture_output=True, text=True, timeout=30,
                         env={**os.environ, "HOME": str(tmp_path), "ZED_WORKTREE_ROOT": str(ROOT),
                              "ZED_FILE": str(ROOT / "backend" / "tests" / "test_csv_and_mask.py")})
    assert res.returncode == 0, res.stderr
    assert "cd backend && " in res.stdout and res.stdout.split("\n")[0].endswith("-k test_x")


def test_setup_task_is_offered_in_both_editors_and_plans_safely():
    vscode, zed = _tasks()
    assert vscode[0]["command"].endswith("/tests/setup-dev.sh") and zed[0]["command"].endswith("/tests/setup-dev.sh")
    res = subprocess.run([str(ROOT / "tests" / "setup-dev.sh"), "--plan"], cwd=ROOT, capture_output=True, text=True,
                         timeout=60)
    assert res.returncode == 0, res.stderr
    lines = res.stdout
    assert "-r " + str(ROOT / "backend" / "requirements.lock") in lines
    lock_line = next(ln for ln in lines.splitlines() if "requirements.lock" in ln)
    assert "pytest" not in lock_line, "lock (avec empreintes) et outils de test : deux commandes pip séparées"
    assert "pytest>=8" in lines


def test_vscode_interpreter_points_to_the_venv_created_by_setup():
    for comp, prefix in (("backend", "${workspaceFolder}/../"), ("agent", "${workspaceFolder}/../"), (".", "${workspaceFolder}/")):
        s = json.loads((ROOT / comp / ".vscode" / "settings.json").read_text())
        assert s["python.defaultInterpreterPath"] == prefix + ".venv/bin/python"
    assert 'VENV="$ROOT/.venv"' in (ROOT / "tests" / "setup-dev.sh").read_text()


# ------------------------------------------------------------------ débogueur : point d'arrêt réellement atteint

def _dap_send(sock, seq, command, arguments=None):
    body = json.dumps({"seq": seq, "type": "request", "command": command, "arguments": arguments or {}}).encode()
    sock.sendall(b"Content-Length: %d\r\n\r\n" % len(body) + body)


def _dap_read(sock, buf):
    while b"\r\n\r\n" not in buf[0]:
        chunk = sock.recv(65536)
        if not chunk:
            raise EOFError
        buf[0] += chunk
    header, rest = buf[0].split(b"\r\n\r\n", 1)
    length = int(re.search(rb"Content-Length: (\d+)", header).group(1))
    while len(rest) < length:
        rest += sock.recv(65536)
    buf[0] = rest[length:]
    return json.loads(rest[:length])


@pytest.mark.skipif(subprocess.run(["python3", "-c", "import debugpy, pytest_cov"], capture_output=True).returncode,
                    reason="debugpy absent (installé par tests/setup-dev.sh)")
def test_breakpoint_in_application_code_is_hit_through_the_debug_launcher():
    """Ce que font Zed et VS Code : debugpy lance tests/debug_pytest.py ; un point d'arrêt dans le CODE TESTÉ
    (backend/app/core/csv_export.py) doit arrêter l'exécution, avec les variables du cadre lisibles."""
    import socket
    import time
    target = ROOT / "backend" / "app" / "core" / "csv_export.py"
    line = next(i for i, ln in enumerate(target.read_text().splitlines(), 1) if ln.strip() == 'if value is None:')
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen(["python3", "-m", "debugpy", "--listen", f"127.0.0.1:{port}", "--wait-for-client",
                             str(ROOT / "tests" / "debug_pytest.py"), "backend/tests/test_csv_and_mask.py",
                             "-k", "neutralize"], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        for _ in range(100):
            try:
                sock = socket.create_connection(("127.0.0.1", port), timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        sock.settimeout(30)
        buf, seq = [b""], 1
        _dap_send(sock, seq, "initialize", {"adapterID": "debugpy", "clientID": "akwaba-test"})
        seq += 1
        _dap_send(sock, seq, "attach", {"justMyCode": False})
        stopped, frame_line, frame_file, variables = None, None, None, {}
        while True:
            msg = _dap_read(sock, buf)
            if msg.get("type") == "event" and msg["event"] == "initialized":
                seq += 1
                _dap_send(sock, seq, "setBreakpoints", {"source": {"path": str(target)}, "breakpoints": [{"line": line}]})
                seq += 1
                _dap_send(sock, seq, "configurationDone")
            elif msg.get("type") == "event" and msg["event"] == "stopped":
                stopped = msg["body"]
                seq += 1
                _dap_send(sock, seq, "stackTrace", {"threadId": stopped["threadId"], "levels": 1})
            elif msg.get("type") == "response" and msg["command"] == "stackTrace":
                top = msg["body"]["stackFrames"][0]
                frame_line, frame_file = top["line"], top["source"]["path"]
                seq += 1
                _dap_send(sock, seq, "scopes", {"frameId": top["id"]})
            elif msg.get("type") == "response" and msg["command"] == "scopes":
                seq += 1
                _dap_send(sock, seq, "variables", {"variablesReference": msg["body"]["scopes"][0]["variablesReference"]})
            elif msg.get("type") == "response" and msg["command"] == "variables":
                variables = {v["name"]: v["value"] for v in msg["body"]["variables"]}
                seq += 1
                _dap_send(sock, seq, "disconnect", {"terminateDebuggee": True})
                break
        sock.close()
    finally:
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
    assert stopped and stopped["reason"] == "breakpoint", stopped
    assert pathlib.Path(frame_file).resolve() == target.resolve() and frame_line == line
    assert "value" in variables, variables


def test_debug_launcher_routes_like_run_sh():
    cases = {
        ("backend/tests/test_csv_and_mask.py", "TestX > test_y"): "cd backend && pytest tests/test_csv_and_mask.py -k test_y",
        (str(ROOT / "agent" / "tests"), ""): "cd agent && pytest tests",
        ("tests/e2e/browser_11c.mjs", ""): "cd . && pytest -q -p no:cacheprovider tests/e2e/test_dashboard_11c.py",
    }
    for (target, k), expected in cases.items():
        args = ["python3", str(ROOT / "tests" / "debug_pytest.py"), target] + (["-k", k] if k else [])
        res = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, env={**os.environ, "AKWABA_DEBUG_PLAN": "1"})
        assert res.stdout.strip() == expected, (target, res.stdout, res.stderr)
    res = subprocess.run(["python3", str(ROOT / "tests" / "debug_pytest.py"), "frontend/tests/lot11d.test.mjs"],
                         cwd=ROOT, capture_output=True, text=True)
    assert res.returncode != 0 and "configuration « Node »" in res.stderr


def test_coverage_writes_editor_readable_reports(tmp_path):
    res = subprocess.run([str(RUN), "--couverture", "backend/tests/test_csv_and_mask.py", "-k", "neutralize"],
                         cwd=ROOT, capture_output=True, text=True, timeout=120,
                         env={**os.environ, "AKWABA_PYTHON": "python3"})
    assert res.returncode == 0, res.stdout + res.stderr
    lcov = (ROOT / "backend" / "couverture" / "lcov.info").read_text()
    assert "SF:app/core/csv_export.py" in lcov, "chemins relatifs au dossier backend (ouvert comme dossier VS Code)"
    assert (ROOT / "backend" / "couverture" / "html" / "index.html").exists()
    res = subprocess.run([str(RUN), "--couverture", "frontend/tests/lot11d.test.mjs"], cwd=ROOT, capture_output=True,
                         text=True, timeout=120)
    assert res.returncode == 0, res.stdout + res.stderr
    front = (ROOT / "frontend" / "couverture" / "lcov.info").read_text()
    assert "SF:assets/js/" in front and "SF:tests/" not in front


def test_debug_configurations_use_the_launcher_and_the_venv():
    vs = json.loads((ROOT / ".vscode" / "launch.json").read_text())["configurations"]
    zed = json.loads((ROOT / ".zed" / "debug.json").read_text())
    py_vs = [c for c in vs if c["type"] == "debugpy"]
    py_zed = [c for c in zed if c["adapter"] == "Debugpy"]
    assert len(py_vs) == 2 and len(py_zed) == 2
    for c in py_vs:
        assert c["program"] == "${workspaceFolder}/tests/debug_pytest.py" and c["justMyCode"] is False
        assert c["python"] == "${workspaceFolder}/.venv/bin/python" and c["args"][0] == "${file}"
    for c in py_zed:
        assert c["program"] == "$ZED_WORKTREE_ROOT/tests/debug_pytest.py" and c["request"] == "launch"
        assert c["python"] == "$ZED_WORKTREE_ROOT/.venv/bin/python" and c["args"][0] == "$ZED_FILE"
    assert py_zed[1]["args"] == ["$ZED_FILE", "-k", "${ZED_SYMBOL:}"]
    node_vs = next(c for c in vs if c["type"] == "node")
    node_zed = next(c for c in zed if c["adapter"] == "JavaScript")
    assert node_vs["runtimeArgs"] == ["--test"] == node_zed["runtimeArgs"]
    assert node_vs["cwd"].endswith("/frontend") and node_zed["cwd"].endswith("/frontend")
    assert all(c.get("label") for c in zed), "Zed exige un label par configuration"


def test_coverage_and_debugger_tools_are_installed_by_setup_and_offered_as_tasks():
    setup = (ROOT / "tests" / "setup-dev.sh").read_text()
    assert '"pytest-cov>=5"' in setup and '"debugpy>=1.8"' in setup
    vscode, zed = _tasks()
    for tasks in (vscode, zed):
        labels = [t["label"] for t in tasks]
        assert "Akwaba : couverture du fichier courant" in labels
        assert "Akwaba : couverture complète (backend, agent, frontend)" in labels
    rec = json.loads((ROOT / ".vscode" / "extensions.json").read_text())["recommendations"]
    assert {"ms-python.debugpy", "ryanluker.vscode-coverage-gutters"} <= set(rec)
    for comp, pkg in (("backend", "app"), ("agent", "akwaba_agent")):
        rc = (ROOT / comp / ".coveragerc").read_text()
        assert f"source = {pkg}" in rc and "branch = True" in rc
