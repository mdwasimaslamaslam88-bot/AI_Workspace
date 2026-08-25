from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any


MAX_GENERATED_CODE_CHARACTERS = 24_000
MAX_VERIFIER_OUTPUT_CHARACTERS = 4_000
SANDBOX_TIMEOUT_SECONDS = 20


@dataclass(frozen=True, slots=True)
class CodeGenerationCase:
    test_id: str
    language: str
    prompt: str
    filename: str
    verifier_filename: str | None
    verifier_source: str
    commands: tuple[tuple[str, ...], ...]
    expected_stdout: str = "PASS"
    model_role: str = "coder"


def build_code_generation_cases(repository_root: Path) -> tuple[CodeGenerationCase, ...]:
    node = shutil.which("node")
    rustc = shutil.which("rustc")
    if node is None or rustc is None:
        raise RuntimeError("required local code verifier compiler is unavailable")
    tsc = str(repository_root / "node_modules" / "typescript" / "bin" / "tsc")
    return (
        CodeGenerationCase(
            "codegen-python-clamp",
            "python",
            """Write Python code only. Implement `clamp(value, low, high)`.
Return low below the range, high above it, otherwise value. Raise ValueError
when low is greater than high. Do not access files, network, or processes.""",
            "artifact.py",
            "verify.py",
            """from artifact import clamp
assert clamp(-3, 0, 5) == 0
assert clamp(9, 0, 5) == 5
assert clamp(3, 0, 5) == 3
try:
    clamp(1, 4, 2)
except ValueError:
    pass
else:
    raise AssertionError("missing ValueError")
print("PASS")
""",
            (
                ("/usr/bin/python3", "-I", "-m", "py_compile", "artifact.py"),
                ("/usr/bin/python3", "verify.py"),
            ),
        ),
        CodeGenerationCase(
            "codegen-python-merge-intervals",
            "python",
            """Write Python code only. Implement `merge_intervals(intervals)` for
integer [start, end] pairs supplied as lists or tuples. Reject start greater
than end with ValueError,
merge overlapping or touching intervals, do not mutate input, and return a
sorted list of newly created tuples, including for unmerged pairs. Do not
access files, network, or processes.""",
            "artifact.py",
            "verify.py",
            """from artifact import merge_intervals
source = [[5, 7], [1, 3], [3, 4], [10, 10]]
assert merge_intervals(source) == [(1, 4), (5, 7), (10, 10)]
assert source == [[5, 7], [1, 3], [3, 4], [10, 10]]
assert merge_intervals([]) == []
try:
    merge_intervals([[3, 2]])
except ValueError:
    pass
else:
    raise AssertionError("missing ValueError")
print("PASS")
""",
            (
                ("/usr/bin/python3", "-I", "-m", "py_compile", "artifact.py"),
                ("/usr/bin/python3", "verify.py"),
            ),
            model_role="general",
        ),
        CodeGenerationCase(
            "codegen-javascript-unique-sorted",
            "javascript",
            """Write a complete CommonJS JavaScript module only. Export
`uniqueSorted(values)`, returning unique finite numbers in ascending order
without mutating input. Throw TypeError for non-arrays or non-finite entries.
Export it as the property `module.exports = { uniqueSorted }`. Do not access
files, network, or child processes.""",
            "artifact.cjs",
            "verify.cjs",
            """const { uniqueSorted } = require('./artifact.cjs');
const source = [3, 1, 3, -1, 2];
if (JSON.stringify(uniqueSorted(source)) !== '[-1,1,2,3]') throw Error('value');
if (JSON.stringify(source) !== '[3,1,3,-1,2]') throw Error('mutation');
for (const bad of [null, [1, Infinity], [NaN]]) {
  try { uniqueSorted(bad); throw Error('accepted'); } catch (error) {
    if (error.message === 'accepted') throw error;
  }
}
console.log('PASS');
""",
            ((node, "--check", "artifact.cjs"), (node, "verify.cjs")),
        ),
        CodeGenerationCase(
            "codegen-javascript-state-machine",
            "javascript",
            """Write a complete CommonJS JavaScript module only. Export
`nextState(state, event)` for states idle/running/done. start moves idle to
running; finish moves running to done; reset moves any state to idle. Throw
Error for every other transition. No files, network, or child processes.""",
            "artifact.cjs",
            "verify.cjs",
            """const { nextState } = require('./artifact.cjs');
if (nextState('idle', 'start') !== 'running') throw Error('start');
if (nextState('running', 'finish') !== 'done') throw Error('finish');
if (nextState('done', 'reset') !== 'idle') throw Error('reset');
for (const pair of [['idle','finish'], ['bad','reset']]) {
  try { nextState(...pair); throw Error('accepted'); } catch (error) {
    if (error.message === 'accepted') throw error;
  }
}
console.log('PASS');
""",
            ((node, "--check", "artifact.cjs"), (node, "verify.cjs")),
        ),
        CodeGenerationCase(
            "codegen-typescript-port",
            "typescript",
            """Write TypeScript code only. Export `parsePort(value: string): number`.
Accept canonical decimal strings from 1 through 65535. Reject whitespace,
signs, leading zeroes, decimals, and out-of-range values with RangeError. Do
not trim or normalize input. Do not access files, network, or processes.""",
            "artifact.ts",
            "verify.ts",
            """import { parsePort } from './artifact';
if (parsePort('1') !== 1 || parsePort('65535') !== 65535) throw Error('valid');
for (const value of ['0','65536','01',' 80','80 ','+80','1.5','abc']) {
  try { parsePort(value); throw Error('accepted'); } catch (error) {
    if (error instanceof Error && error.message === 'accepted') throw error;
  }
}
console.log('PASS');
""",
            (
                (
                    node,
                    tsc,
                    "--strict",
                    "--target",
                    "ES2022",
                    "--module",
                    "commonjs",
                    "--outDir",
                    "out",
                    "artifact.ts",
                    "verify.ts",
                ),
                (node, "out/verify.js"),
            ),
        ),
        CodeGenerationCase(
            "codegen-typescript-chunks",
            "typescript",
            """Write TypeScript code only. Export generic function
`chunks<T>(values: readonly T[], size: number): T[][]`. Preserve order, never
mutate input, and throw RangeError unless size is a positive integer. Do not
access files, network, or processes.""",
            "artifact.ts",
            "verify.ts",
            """import { chunks } from './artifact';
const source = [1,2,3,4,5] as const;
if (JSON.stringify(chunks(source, 2)) !== '[[1,2],[3,4],[5]]') throw Error('value');
if (JSON.stringify(source) !== '[1,2,3,4,5]') throw Error('mutation');
for (const value of [0, -1, 1.5]) {
  try { chunks(source, value); throw Error('accepted'); } catch (error) {
    if (error instanceof Error && error.message === 'accepted') throw error;
  }
}
console.log('PASS');
""",
            (
                (
                    node,
                    tsc,
                    "--strict",
                    "--target",
                    "ES2022",
                    "--module",
                    "commonjs",
                    "--outDir",
                    "out",
                    "artifact.ts",
                    "verify.ts",
                ),
                (node, "out/verify.js"),
            ),
        ),
        CodeGenerationCase(
            "codegen-rust-gcd",
            "rust",
            """Write Rust code only. Implement `fn gcd(a: u64, b: u64) -> u64`
using Euclid's algorithm. It must handle zero arguments and perform no I/O,
filesystem, network, or process operations. Define gcd(0, 0) as 0.""",
            "artifact.rs",
            None,
            """
#[cfg(test)]
mod codex_verifier {
    use super::*;
    #[test]
    fn objective_cases() {
        assert_eq!(gcd(48, 18), 6);
        assert_eq!(gcd(0, 9), 9);
        assert_eq!(gcd(0, 0), 0);
        assert_eq!(gcd(17, 13), 1);
    }
}
""",
            (
                (
                    rustc,
                    "--edition=2021",
                    "--test",
                    "combined.rs",
                    "-o",
                    "rust-tests",
                ),
                ("./rust-tests", "--quiet"),
            ),
            "",
        ),
        CodeGenerationCase(
            "codegen-rust-transition",
            "rust",
            """Write Rust code only. Define enums State { Idle, Running, Done }
and Event { Start, Finish, Reset }, then implement
`fn next_state(state: State, event: Event) -> Result<State, &'static str>`.
Allow Idle+Start, Running+Finish, and any state+Reset; reject others. Derive
Debug, PartialEq, Eq, Copy, Clone. Perform no I/O, network, or process work.""",
            "artifact.rs",
            None,
            """
#[cfg(test)]
mod codex_verifier {
    use super::*;
    #[test]
    fn objective_cases() {
        assert_eq!(next_state(State::Idle, Event::Start), Ok(State::Running));
        assert_eq!(next_state(State::Running, Event::Finish), Ok(State::Done));
        assert_eq!(next_state(State::Done, Event::Reset), Ok(State::Idle));
        assert!(next_state(State::Idle, Event::Finish).is_err());
    }
}
""",
            (
                (
                    rustc,
                    "--edition=2021",
                    "--test",
                    "combined.rs",
                    "-o",
                    "rust-tests",
                ),
                ("./rust-tests", "--quiet"),
            ),
            "",
        ),
        CodeGenerationCase(
            "codegen-bash-slug",
            "bash",
            """Write Bash code only defining `is_slug`. It must return success
only when its single argument matches lowercase ASCII letters or digits joined
by single hyphens (examples: alpha, a1-b2). It must reject empty input,
uppercase, underscores, leading/trailing hyphens, repeated hyphens, and extra
arguments. Include no example calls or test invocations. Use only Bash
builtins; no files, network, eval, or subprocesses.""",
            "artifact.sh",
            "verify.sh",
            """set -euo pipefail
source ./artifact.sh
for value in alpha a1-b2 z9; do is_slug "$value"; done
for value in '' Alpha a_b -alpha alpha- alpha--beta; do
  if is_slug "$value"; then exit 31; fi
done
if is_slug alpha beta; then exit 32; fi
printf 'PASS\n'
""",
            (
                ("/usr/bin/bash", "--noprofile", "--norc", "-n", "artifact.sh"),
                ("/usr/bin/bash", "--noprofile", "--norc", "verify.sh"),
            ),
        ),
        CodeGenerationCase(
            "codegen-bash-join",
            "bash",
            """Write Bash code only defining `join_by_comma`. Print all arguments
joined by a comma and one space, followed by exactly one newline. With no
arguments print only a newline. Preserve spaces and glob characters literally.
Use only Bash builtins; include no example calls or test invocations; and do
not access files, network, eval, or subprocesses.""",
            "artifact.sh",
            "verify.sh",
            """set -euo pipefail
source ./artifact.sh
[[ "$(join_by_comma alpha 'two words' '*')" == 'alpha, two words, *' ]]
[[ "$(join_by_comma one)" == 'one' ]]
[[ "$(join_by_comma)" == '' ]]
printf 'PASS\n'
""",
            (
                ("/usr/bin/bash", "--noprofile", "--norc", "-n", "artifact.sh"),
                ("/usr/bin/bash", "--noprofile", "--norc", "verify.sh"),
            ),
        ),
        CodeGenerationCase(
            "codegen-sql-aggregation",
            "sql",
            """Write one SQLite SELECT statement only. Table sales(customer TEXT,
amount INTEGER) exists. Return customer and total_amount for customers whose
sum is at least 10, ordered by total_amount descending then customer ascending.
Do not modify schema/data, attach databases, load extensions, or access files.""",
            "artifact.sql",
            None,
            """CREATE TABLE sales(customer TEXT, amount INTEGER);
INSERT INTO sales VALUES ('ada',7),('ada',5),('bob',9),('cy',10),('cy',2);
.mode list
.separator |
""",
            (("/usr/bin/sqlite3", ":memory:"),),
            "ada|12\ncy|12",
        ),
        CodeGenerationCase(
            "codegen-sql-null-safe",
            "sql",
            """Write one SQLite SELECT statement only. Table readings(sensor TEXT,
value INTEGER) exists. Return sensor and the count of non-null values as
sample_count for every sensor, including zero counts, ordered by sensor.
Do not modify schema/data, attach databases, load extensions, or access files.""",
            "artifact.sql",
            None,
            """CREATE TABLE readings(sensor TEXT, value INTEGER);
INSERT INTO readings VALUES ('a',1),('a',NULL),('b',NULL),('c',2),('c',3);
.mode list
.separator |
""",
            (("/usr/bin/sqlite3", ":memory:"),),
            "a|1\nb|0\nc|2",
        ),
    )


def extract_generated_artifact(answer: str, language: str) -> str:
    if not isinstance(answer, str) or len(answer) > MAX_GENERATED_CODE_CHARACTERS:
        raise ValueError("generated artifact is blank or too large")
    fences = re.findall(r"```(?:[A-Za-z0-9_+.-]+)?\s*\n(.*?)```", answer, re.DOTALL)
    artifact = fences[0].strip() if fences else answer.strip()
    if not artifact:
        raise ValueError("generated artifact is blank")
    if len(fences) > 1:
        raise ValueError("generated answer contains multiple artifacts")
    return artifact


def _static_safety_failure(language: str, artifact: str) -> str | None:
    common = (
        r"/etc/|/home/|\.ssh|BEGIN PRIVATE KEY|\b(?:curl|wget|nc|ssh)\b",
        r"\b(?:eval|exec)\s*\(",
    )
    language_patterns = {
        "python": (r"\b(?:import|from)\s+(?:os|subprocess|socket|pathlib|requests|httpx)\b", r"\bopen\s*\("),
        "javascript": (r"\brequire\s*\(\s*['\"](?:fs|net|http|https|child_process|worker_threads)['\"]", r"\bfetch\s*\("),
        "typescript": (r"\b(?:import|require)\b[^\n]*(?:fs|net|http|https|child_process)", r"\bfetch\s*\("),
        "rust": (r"\bstd::(?:fs|net|process)\b", r"\bCommand\s*::"),
        "bash": (r"\b(?:source|\.)\s+/(?:etc|home|proc)\b", r"\b(?:rm|dd|chmod|chown)\b"),
        "sql": (r"\b(?:insert|update|delete|drop|alter|attach|detach|pragma|vacuum|load_extension)\b",),
    }
    for pattern in (*common, *language_patterns[language]):
        if re.search(pattern, artifact, re.IGNORECASE):
            return f"static_safety_rejection:{pattern}"
    if language == "sql":
        stripped = artifact.strip().rstrip(";").strip()
        if not re.match(r"^(?:select|with)\b", stripped, re.IGNORECASE):
            return "static_safety_rejection:sql_not_read_only"
        if ";" in stripped:
            return "static_safety_rejection:multiple_sql_statements"
    return None


def _run_sandboxed(
    command: tuple[str, ...],
    root: Path,
    *,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    sandbox = (
        "/usr/bin/systemd-run",
        "--user",
        "--quiet",
        "--wait",
        "--pipe",
        "--collect",
        "--property=PrivateNetwork=yes",
        "--property=RestrictAddressFamilies=AF_UNIX",
        "--property=NoNewPrivileges=yes",
        "--property=PrivateTmp=yes",
        "--property=MemoryMax=512M",
        "--property=TasksMax=64",
        f"--property=RuntimeMaxSec={SANDBOX_TIMEOUT_SECONDS}s",
        f"--working-directory={root}",
        "/usr/bin/env",
        "-i",
        "PATH=/usr/bin:/bin",
        *command,
    )
    return subprocess.run(
        sandbox,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=SANDBOX_TIMEOUT_SECONDS + 5,
        check=False,
    )


def verify_generated_code(
    case: CodeGenerationCase,
    answer: str,
) -> dict[str, Any]:
    try:
        artifact = extract_generated_artifact(answer, case.language)
    except ValueError as exc:
        return {"passed": False, "failure_reason": str(exc), "evidence": []}
    safety_failure = _static_safety_failure(case.language, artifact)
    if safety_failure is not None:
        return {
            "passed": False,
            "failure_reason": safety_failure,
            "evidence": [],
            "static_safety_passed": False,
        }

    evidence: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="work-station-codegen.") as temporary:
        root = Path(temporary)
        (root / case.filename).write_text(artifact + "\n", encoding="utf-8")
        if case.verifier_filename is not None:
            (root / case.verifier_filename).write_text(
                case.verifier_source,
                encoding="utf-8",
            )
        if case.language == "rust":
            (root / "combined.rs").write_text(
                artifact + "\n" + case.verifier_source,
                encoding="utf-8",
            )
        sql_input = None
        if case.language == "sql":
            sql_input = case.verifier_source + artifact + "\n"

        for command in case.commands:
            try:
                completed = _run_sandboxed(command, root, input_text=sql_input)
            except subprocess.TimeoutExpired:
                return {
                    "passed": False,
                    "failure_reason": "verifier_timeout",
                    "evidence": evidence,
                    "static_safety_passed": True,
                }
            stdout = completed.stdout[-MAX_VERIFIER_OUTPUT_CHARACTERS:].strip()
            stderr = completed.stderr[-MAX_VERIFIER_OUTPUT_CHARACTERS:].strip()
            stdout = stdout.replace(str(root), "<sandbox>")
            stderr = stderr.replace(str(root), "<sandbox>")
            evidence.append(
                {
                    "command": Path(command[0]).name,
                    "exit_code": completed.returncode,
                    "stdout": stdout,
                    "stderr": stderr,
                }
            )
            if completed.returncode != 0:
                return {
                    "passed": False,
                    "failure_reason": "compile_or_execution_failure",
                    "evidence": evidence,
                    "static_safety_passed": True,
                }

    observed = evidence[-1]["stdout"] if evidence else ""
    passed = case.expected_stdout == "" or observed == case.expected_stdout
    return {
        "passed": passed,
        "failure_reason": None if passed else "objective_output_mismatch",
        "evidence": evidence,
        "static_safety_passed": True,
        "artifact_characters": len(artifact),
    }
