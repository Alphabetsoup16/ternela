# SPDX-License-Identifier: Apache-2.0
"""Full checks by default; an incremental foreground loop reuses local evidence."""

import argparse
import ast
import json
import sqlite3
import subprocess
import sys
import tempfile
import time
import tomllib
from collections import defaultdict
from contextlib import closing
from pathlib import Path

from ternela.distribution import FILES, ROOTS, SUFFIXES
from ternela.storage import PROJECT, canonical, sha, strict_json, write_json

TEST_DIRS = ("tests", "safety_lab/tests", "workbench/tests")
FORBIDDEN_PROVIDER_IMPORTS = (
    "workbench",
    "ternela.local_service",
    "ternela.acquisition",
    "ternela.watches",
    "ternela.repository",
    "ternela.database",
    "ternela.migrations",
    "ternela.schedules",
    "ternela.monitor",
    "ternela.local_control",
    "ternela.transport",
    "threading",
    "asyncio",
    "sched",
    "subprocess",
    "socket",
    "http.client",
    "urllib.request",
    "requests",
    "httpx",
    "importlib",
)
CHILD = """
import json,sys,unittest
sys.path.insert(0,sys.argv.pop(1))
loader=unittest.TestLoader()
suite=unittest.TestSuite(loader.discover('.',pattern=name) for name in sys.argv[1:])
result=unittest.TextTestRunner(verbosity=1).run(suite)
print(json.dumps({'tests':result.testsRun,'success':result.wasSuccessful()}))
sys.exit(0 if result.wasSuccessful() else 1)
"""


def snapshot(root):
    paths = {root / name for name in (*FILES, "AGENTS.md", "package_build.py", "MANIFEST.in")}
    for directory in ROOTS:
        paths.update(
            p
            for p in (root / directory).rglob("*")
            if p.suffix in SUFFIXES
            and not any(part.startswith((".", "__pycache__")) for part in p.relative_to(root).parts)
        )
    paths.update((root / "data/sources").glob("*"))
    return {
        p.relative_to(root).as_posix(): sha(p.read_bytes())
        for p in sorted(paths)
        if p.is_file() and not p.is_symlink()
    }


def source_stamp(root, names):
    result = {}
    for name in names:
        stat = (root / name).stat()
        result[name] = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
    return result


def imports(path, tree):
    module = path.removesuffix(".py").replace("/", ".")
    package = (
        module.removesuffix(".__init__")
        if path.endswith("/__init__.py")
        else module.rpartition(".")[0]
    )
    result = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = node.module or ""
            if node.level:
                parts = package.split(".")
                prefix = ".".join(
                    parts[: len(parts) - node.level + 1] + ([prefix] if prefix else [])
                )
            result.add(prefix)
            result.update(prefix + "." + alias.name for alias in node.names)
    return result


def boundary_issues(root):
    issues = []
    for path in sorted((root / "ternela/providers").glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        names = imports(path.relative_to(root).as_posix(), tree)
        for name in sorted(names):
            if any(other.startswith(name + ".") for other in names):
                continue
            if any(
                name == forbidden or name.startswith(forbidden + ".")
                for forbidden in FORBIDDEN_PROVIDER_IMPORTS
            ):
                issues.append(
                    f"{path.relative_to(root)} imports {name}; acquisition, scheduling and delivery belong to the shared runtime"
                )
        if any(
            isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "__import__"
            for n in ast.walk(tree)
        ):
            issues.append(
                f"{path.relative_to(root)} uses dynamic imports; provider dependencies must be explicit"
            )
    # Follow actual core imports, so a new transitive helper cannot escape the rule.
    pending = [
        "ternela",
        "ternela.client",
        "ternela.watches",
        "ternela.monitor",
        "ternela.compiler.jobs",
        "ternela.compiler.run",
        "ternela.compiler.guides",
    ]
    seen = set()
    while pending:
        module = pending.pop()
        if module in seen:
            continue
        seen.add(module)
        path = root / (module.replace(".", "/") + ".py")
        if not path.is_file():
            path = root / module.replace(".", "/") / "__init__.py"
        if not path.is_file():
            continue
        names = imports(
            path.relative_to(root).as_posix(), ast.parse(path.read_text(), filename=str(path))
        )
        for name in sorted(names):
            if (
                name == "workbench"
                or name.startswith("workbench.")
                or name.split(".")[0].endswith("_lab")
            ):
                issues.append(
                    f"{path.relative_to(root)} imports {name}; stable runtime must not depend on interfaces or experiment packages"
                )
            if name == "ternela" or name.startswith("ternela."):
                pending.append(name)
    return issues


def test_files(root):
    return sorted(
        p.relative_to(root).as_posix()
        for directory in TEST_DIRS
        for p in (root / directory).glob("test_*.py")
    )


def select_tests(root, current, baseline):
    tests = test_files(root)
    if baseline is None:
        return tests, "full: no successful baseline"
    changed = {
        name for name in current.keys() | baseline.keys() if current.get(name) != baseline.get(name)
    }
    if not changed:
        return [], "unchanged source"
    if any(
        name not in current or name not in baseline or not name.endswith(".py") for name in changed
    ):
        return tests, "full: added/deleted source, configuration, asset or fixture"
    modules, trees = {}, {}
    for name in current:
        if not name.endswith(".py"):
            continue
        tree = ast.parse((root / name).read_text(), filename=name)
        if any(
            isinstance(n, ast.Call)
            and (
                (isinstance(n.func, ast.Name) and n.func.id in ("__import__", "exec", "eval"))
                or (
                    isinstance(n.func, ast.Attribute)
                    and n.func.attr in ("import_module", "spec_from_file_location")
                )
            )
            for n in ast.walk(tree)
        ):
            return tests, "full: dynamic module loading"
        module = name.removesuffix(".py").replace("/", ".").removesuffix(".__init__")
        modules[module] = name
        trees[name] = tree
    reverse = defaultdict(set)
    for name, tree in trees.items():
        targets = imports(name, tree)
        # Patch targets and explicitly named local modules may be loaded by tests.
        targets.update(
            n.value
            for n in ast.walk(tree)
            if isinstance(n, ast.Constant)
            and isinstance(n.value, str)
            and "/" not in n.value
            and " " not in n.value
        )
        for target in targets:
            parts = target.split(".")
            for length in range(1, len(parts) + 1):
                dependency = modules.get(".".join(parts[:length]))
                if dependency:
                    reverse[dependency].add(name)
    affected, pending = set(changed), list(changed)
    while pending:
        for dependent in reverse[pending.pop()] - affected:
            affected.add(dependent)
            pending.append(dependent)
    selected = [name for name in tests if name in affected]
    return (
        (selected, "local import dependents")
        if selected
        else (tests, "full: no mapped dependent test")
    )


def run_tests(root, selected):
    groups = defaultdict(list)
    for name in selected:
        path = Path(name)
        groups[path.parent.as_posix()].append(path.name)
    total, success = 0, True
    # Empty fresh caches prevent same-second/same-size edits from reusing stale pyc files.
    with tempfile.TemporaryDirectory(prefix="ternela-check-pyc-") as cache:
        for directory, names in sorted(groups.items()):
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-X",
                    "pycache_prefix=" + cache,
                    "-W",
                    "error::ResourceWarning",
                    "-c",
                    CHILD,
                    str(root),
                    *names,
                ],
                cwd=root / directory,
                capture_output=True,
                text=True,
                timeout=120,
            )
            try:
                detail = json.loads(result.stdout.splitlines()[-1])
                total += detail["tests"]
            except (IndexError, ValueError, KeyError):
                detail = {"tests": 0}
            passed = result.returncode == 0 and detail["tests"] > 0
            success = success and passed
            print(
                f"  {directory}: {detail['tests']} tests {'passed' if passed else 'FAILED'}",
                flush=True,
            )
            if not passed:
                print(result.stdout + result.stderr, flush=True)
    return {"tests": total, "files": len(selected), "success": success}


def evidence_stamp(data):
    path = data / "workbench/watches.sqlite3"
    if not path.exists():
        return None
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        return [
            path.stat().st_dev,
            path.stat().st_ino,
            db.execute("SELECT coalesce(max(rowid),0) FROM events").fetchone()[0],
        ]


def cycle(root=PROJECT, data=None, *, incremental=False, runner=run_tests):
    from ternela.replay import compare, harvest, load_corpus

    root, data = Path(root), Path(data or root / "data")
    started = time.perf_counter()
    baseline_path = data / "checks/baseline.json"
    report = {"success": False}
    try:
        current = snapshot(root)
        initial_stamp = source_stamp(root, current)
        saved = strict_json(baseline_path.read_bytes()) if baseline_path.exists() else None
        if saved is not None and (
            saved.get("format") != 1 or not isinstance(saved.get("files"), dict)
        ):
            raise ValueError("Unsupported successful-check baseline")
        environment = dict(
            python=sys.version, executable=sys.executable, prefix=sys.prefix, platform=sys.platform
        )
        environment_changed = saved is not None and saved.get("environment") != environment
        baseline = saved["files"] if incremental and saved and not environment_changed else None
        dependencies = tomllib.loads((root / "pyproject.toml").read_text())["project"].get(
            "dependencies", []
        )
        dependency_change = (
            {"before": saved["runtime_dependencies"], "after": dependencies}
            if saved and dependencies != saved["runtime_dependencies"]
            else None
        )
        selected, reason = select_tests(root, current, baseline)
        issues = boundary_issues(root)
        corpus = load_corpus(data)
        # Verify old expectations before any retention can evict a failing example.
        old_cases = corpus["cases"]
        reuse = (
            incremental
            and saved
            and saved["files"] == current
            and not environment_changed
            and saved.get("replay_sha256") == sha(canonical(old_cases))
        )
        old = (
            dict(total=len(old_cases), passed=len(old_cases), failures=[])
            if reuse
            else compare(old_cases)
        )
        if old["failures"]:
            replay, capture = old, {"skipped": "existing replay failed; corpus and cursor retained"}
        else:
            cases, capture = harvest(data, corpus)
            previous_ids = {case["id"] for case in old_cases}
            new_cases = [case for case in cases if case["id"] not in previous_ids]
            fresh = compare(new_cases)
            replay = dict(
                total=len(cases),
                passed=len(cases) - len(fresh["failures"]),
                failures=fresh["failures"],
                cached=len(cases) - len(new_cases) if reuse else 0,
                replayed=len(new_cases) + (0 if reuse else len(old_cases)),
            )
        print(
            f"Check: {reason}; {len(selected)} test files; {replay['total']} receipt examples",
            flush=True,
        )
        tests = runner(root, selected) if not issues else {"tests": 0, "files": 0, "success": False}
        stable = snapshot(root) == current and source_stamp(root, current) == initial_stamp
        report.update(
            reason=reason,
            selected=selected,
            tests=tests,
            replay=replay,
            capture=capture,
            boundary_issues=issues,
            source_stable=stable,
            runtime_dependency_change=dependency_change,
            environment_changed=environment_changed,
        )
        report["success"] = tests["success"] and not issues and not replay["failures"] and stable
        if report["success"]:
            write_json(
                baseline_path,
                dict(
                    format=1,
                    files=current,
                    runtime_dependencies=dependencies,
                    environment=environment,
                    replay_sha256=sha(canonical(cases)),
                ),
            )
    except (
        ValueError,
        OSError,
        EOFError,
        KeyError,
        TypeError,
        IndexError,
        AttributeError,
        SyntaxError,
        sqlite3.Error,
        subprocess.TimeoutExpired,
    ) as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    report["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
    write_json(data / "checks/last-run.json", report)
    print(
        f"{'PASS' if report['success'] else 'FAIL'} · {report['elapsed_ms']} ms · {data / 'checks/last-run.json'}",
        flush=True,
    )
    if report.get("error"):
        print(report["error"], flush=True)
    if report.get("runtime_dependency_change"):
        print("Runtime dependencies changed:", report["runtime_dependency_change"], flush=True)
    for issue in report.get("boundary_issues", []):
        print(issue, flush=True)
    for failure in report.get("replay", {}).get("failures", []):
        print(
            "Receipt behavior changed:",
            failure.get("id"),
            failure.get("error")
            or failure.get("issues")
            or f"{failure['expected']['status']} -> {failure['actual']['status']}; see decision/check details in the report",
            flush=True,
        )
    return report


def fresh_cycle(root, data):
    with tempfile.TemporaryDirectory(prefix="ternela-check-watch-") as cache:
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-X",
                "pycache_prefix=" + cache,
                "-m",
                "ternela.check",
                "--changed",
                "--data",
                str(data),
            ],
            cwd=root,
            check=False,
        )
    path = data / "checks/last-run.json"
    report = strict_json(path.read_bytes()) if path.exists() else {}
    if result.returncode and report.get("success", True):
        report = dict(success=False, error="Checker child failed before writing a failure report")
        write_json(path, report)
    return report


def watch_loop(root, data, interval=2, *, run=None, sleep=time.sleep, iterations=None):
    previous, count = None, 0
    while iterations is None or count < iterations:
        try:
            marker = [snapshot(root), evidence_stamp(data)]
            if marker != previous:
                report = (
                    fresh_cycle(root, data) if run is None else run(root, data, incremental=True)
                )
                previous = None if report and report.get("capture", {}).get("pending") else marker
        except (OSError, ValueError, sqlite3.Error) as exc:
            print(f"Waiting for a readable source/evidence snapshot: {exc}", flush=True)
        count += 1
        if iterations is None or count < iterations:
            sleep(interval)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--changed", action="store_true", help="Run affected tests since the last successful check"
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Stay in the foreground and check source/evidence changes",
    )
    parser.add_argument("--interval", type=float, default=2, help="Watch interval in seconds, 1–60")
    parser.add_argument(
        "--data",
        type=Path,
        default=PROJECT / "data",
        help="Local data root containing blobs and workbench/watches.sqlite3",
    )
    args = parser.parse_args()
    args.data = args.data.resolve()
    if not 1 <= args.interval <= 60:
        parser.error("Interval must be 1–60 seconds")
    if args.watch:
        print(
            "Watching source and existing receipts. Ctrl-C stops the loop. No providers or models are called.",
            flush=True,
        )
        try:
            watch_loop(PROJECT, args.data, args.interval)
        except KeyboardInterrupt:
            print("Stopped.")
    else:
        raise SystemExit(0 if cycle(data=args.data, incremental=args.changed)["success"] else 1)


if __name__ == "__main__":
    main()
