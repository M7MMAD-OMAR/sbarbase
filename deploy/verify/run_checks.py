"""Offline source verification with complete logs and explicit stage outcomes."""
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import time

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_TREE_MARKER = Path("/usr/local/share/sbarbase-source-verification/public-tree")
PUBLIC_TREE_MARKER_CONTENT = "public-source-verification-v1\n"
STAGES = (
    ("plan-integrity", ["/usr/bin/python3", "deploy/check_plan.py"]),
    ("python-unit", ["/usr/bin/python3", "deploy/verify/unittest_checks.py"]),
    ("bun-test", ["bun", "test"]),
    ("ui-typecheck", ["bun", "run", "typecheck:ui"]),
    ("control-typecheck", ["bun", "run", "typecheck:control"]),
    ("ui-build", ["bun", "run", "build:ui"]),
    ("python-compile", ["/usr/bin/python3", "-m", "compileall", "-q", "lab", "deploy"]),
)

VERSIONS = (("bun", ["bun", "--version"]), ("git", ["git", "--version"]),
            ("packages", ["dpkg-query", "-W"]))


def plain_terminal_output(output):
    """Normalize terminal controls without changing raw evidence or line counts."""
    strings = (r"(?:\x1b\]|\x9d)[\s\S]*?(?:\x07|\x1b\\|\x9c)"
               r"|(?:\x1b[PX^_]|[\x90\x98\x9e\x9f])[\s\S]*?(?:\x1b\\|\x9c)")
    output = re.sub(strings, lambda match: "\n" * match.group().count("\n"), output)
    # CSI formatting and ordinary ESC sequences, including character-set selection.
    # Unclosed strings/CSI stay visible as unknown controls and fail closed below.
    return re.sub(r"(?:\x1b\[|\x9b)[0-?]*[ -/]*[@-~]|\x1b(?![PX\[\]^_])[ -/]*[@-~]", "", output)


def stage_diagnostics(name, output):
    """Recognized compiler/build diagnostics, preserving their log line numbers."""
    if name not in {"ui-typecheck", "control-typecheck", "ui-build", "python-compile"}:
        return []
    diagnostics = []
    for number, line in enumerate(plain_terminal_output(output).splitlines(), 1):
        if (re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]", line)
                or re.search(r"^\s*(?:warning|warn|error|fatal)(?:\s|:|\[)", line, re.I)
                or re.search(r"^\s*\([!]\)", line)
                or re.search(r"^\s*\[[A-Z][A-Z0-9_]+\]", line)
                or re.search(r":\s*\w*(?:Warning|Error):", line)
                or re.search(r":\s*(?:error|warning)\s+TS[0-9]+\b", line, re.I)):
            diagnostics.append({"line": number, "message": line})
    return diagnostics


def source_digest():
    if ROOT != Path("/opt/sbarbase"):
        raise ValueError("Source hashing is restricted to the distributed public /opt/sbarbase image root")
    try:
        if PUBLIC_TREE_MARKER.is_symlink() or not PUBLIC_TREE_MARKER.is_file():
            raise ValueError("Public verifier image marker is missing or invalid")
        if PUBLIC_TREE_MARKER.read_text() != PUBLIC_TREE_MARKER_CONTENT:
            raise ValueError("Public verifier image marker contents are invalid")
    except (OSError, UnicodeError) as error:
        raise ValueError("Public verifier image marker is unreadable") from error
    digest = hashlib.sha256()
    for path in sorted(ROOT.rglob("*")):
        relative = path.relative_to(ROOT)
        if not path.is_file() or any(part in {"node_modules", "__pycache__"} for part in relative.parts):
            continue
        digest.update(str(relative).encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def main(evidence_path=None):
    os.chdir(ROOT)
    evidence = Path("/evidence") if evidence_path is None else Path(evidence_path)
    evidence.mkdir(exist_ok=True)
    report = {"scope": "offline-linux-source-verification", "source_sha256": source_digest(),
              "platform": platform.platform(), "machine": platform.machine(),
              "python": platform.python_version(), "stages": [], "passed": False,
              "limitations": ["No running Supabase services or Docker daemon are available.",
                              "Warning detection covers recognized UI/compiler formats; other tool logs require review.",
                              "This result does not certify deployment, recovery, HA, or release readiness."]}
    for name, command in VERSIONS:
        result = subprocess.run(command, capture_output=True, check=False)
        (evidence / (name + "-versions.txt")).write_bytes(result.stdout)
        (evidence / (name + "-versions.stderr.bin")).write_bytes(result.stderr)
        result.check_returncode()
        if result.stderr:
            raise RuntimeError("Version command emitted diagnostics: " + name)
    for name, command in STAGES:
        started = time.monotonic()
        print("\nRunning " + name + ": " + " ".join(command), flush=True)
        with (evidence / (name + ".log")).open("w") as log:
            try:
                result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=900)
                status, code = ("passed" if result.returncode == 0 else "failed"), result.returncode
            except (OSError, subprocess.TimeoutExpired) as error:
                log.write("\nHarness execution error: " + str(error) + "\n")
                status, code = "execution-error", None
        output = (evidence / (name + ".log")).read_text()
        if name == "bun-test" and code == 0:
            if re.search(r"(?m)^\s*[1-9][0-9]*\s+(?:skip|todo)\b|^\s*\((?:skip|todo)\)", output):
                status, code = "failed-incomplete-tests", 1
                print("FAIL: Bun skipped or todo tests prevent acceptance", flush=True)
            elif not re.search(r"(?m)^\s*[1-9][0-9]*\s+pass\b", output):
                status, code = "failed-empty-tests", 1
                print("FAIL: Bun did not report any passing tests", flush=True)
        diagnostics = stage_diagnostics(name, output)
        if diagnostics and status == "passed":
            status = "failed-diagnostics"
        print(output, end="", flush=True)
        if diagnostics:
            print(f"FAIL: {name} emitted {len(diagnostics)} build/compiler diagnostics", flush=True)
        report["stages"].append({"name": name, "command": command, "status": status,
                                 "exit_code": code, "diagnostics": diagnostics, "seconds": round(time.monotonic() - started, 2)})
        (evidence / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    report["passed"] = all(stage["status"] == "passed" for stage in report["stages"])
    (evidence / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print("\nPortable source verification " + ("passed." if report["passed"] else "failed."))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
