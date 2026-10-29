"""Timeout guarded execution for the trusted, local single-user demo.

This is not a security sandbox. Output is diagnostic only: scores come from a
separate structured file, checked against process completion by the parent.
"""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from collections import deque
from typing import List, Optional

from tools.grading_contract import normalize_language, normalize_sql_assertions

try:
    import resource
except ImportError:
    resource = None

DEFAULT_TIMEOUT_SECONDS = 10
_OUTPUT_BYTES = 64 * 1024
_RESULT_FILE = "_grading_result.json"
_SECRET_MARKERS = ("API_KEY", "APIKEY", "TOKEN", "SECRET", "PASSWORD", "DATABASE_URL", "GROQ", "OPENROUTER",
                   "SERPER", "APIFY", "GITHUB", "OPENAI", "ANTHROPIC", "LANGCHAIN", "LANGSMITH",
                   "HUGGINGFACE", "HF_")
_SANDBOX_PATH_RE = re.compile(
    r"(?:[A-Za-z]:)?(?:[/\\]+[^/\\\r\n\t'\"()<>,:]+)*[/\\]+mindmorph_grade_[^/\\\r\n\t'\"()<>,:]*"
    r"(?:[/\\]+[^/\\\r\n\t'\"()<>,:]+)*")

_RUNNER_SRC = r'''
import ast, asyncio, importlib, inspect, json, os, traceback
result = dict(version=1, complete=True, passed=0, total=0, failures=[], harness=[])
def save():
    with open('_grading_result.json', 'w', encoding='utf-8') as stream:
        json.dump(result, stream, ensure_ascii=False)
def record(name, exc):
    frames = traceback.extract_tb(exc.__traceback__)
    deepest = os.path.basename(frames[-1].filename) if frames else ''
    reason = None
    if isinstance(exc, SyntaxError) and os.path.basename(exc.filename or '') == 'test_solution.py':
        reason = 'the generated test module is not valid Python'
    elif isinstance(exc, ImportError) and getattr(exc, 'name', None) != 'solution' and deepest != 'solution.py':
        reason = 'the grading environment has no module %r' % getattr(exc, 'name', '?')
    elif isinstance(exc, NameError) and deepest == 'test_solution.py':
        reason = 'the generated test uses a name it never imported'
    elif isinstance(exc, FileNotFoundError):
        reason = 'the generated test expects a data file that does not exist'
    elif isinstance(exc, (KeyboardInterrupt, SystemExit)):
        reason = 'the grading process was interrupted before completing the tests'
    message = name + ': ' + ' '.join(''.join(traceback.format_exception_only(type(exc), exc)).split())
    result['harness' if reason else 'failures'].append(message + (' [' + reason + ']' if reason else ''))
class Assertions(ast.NodeVisitor):
    count = 0
    def visit_Assert(self, node): self.count += 1
    def visit_FunctionDef(self, node): pass
    def visit_AsyncFunctionDef(self, node): pass
    def visit_ClassDef(self, node): pass
    def visit_Lambda(self, node): pass
async def awaited(value): return await value
try:
    with open('test_solution.py', encoding='utf-8') as stream:
        tree = ast.parse(stream.read(), filename='test_solution.py')
    names = list(dict.fromkeys(node.name for node in tree.body
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith('test')))
    assertions = Assertions()
    assertions.visit(tree)
    result['total'] = len(names) + bool(assertions.count)
    if not result['total']:
        result['harness'].append('No executable tests or module-level assertions were generated.')
    else:
        mod = importlib.import_module('test_solution')
        if assertions.count: result['passed'] += 1
        for name in names:
            fn = getattr(mod, name)
            if inspect.isgeneratorfunction(fn) or inspect.isasyncgenfunction(fn):
                result['harness'].append(name + ': generator tests are unsupported; assertions must execute inside a regular or async test function')
                continue
            try:
                inspect.signature(fn).bind()
            except (TypeError, ValueError):
                result['harness'].append(name + ': test functions cannot require arguments or pytest fixtures')
                continue
            try:
                value = fn()
                if inspect.isawaitable(value): value = asyncio.run(awaited(value))
                if inspect.isgenerator(value) or inspect.isasyncgen(value):
                    result['harness'].append(name + ': returned generator tests are unsupported')
                    continue
                result['passed'] += 1
            except BaseException as exc: record(name, exc)
except BaseException as exc: record('collect', exc)
save()
'''

# Generated tests load once in the node:test child; legacy functions join its lifecycle.
_JS_RUNNER_SRC = r'''
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');
function reason(error) {
  const stack = String(error.stack || '');
  const inSolution = /(?:^|[\\/])solution\.js:\d/.test(stack);
  if (error instanceof SyntaxError && !inSolution) return 'the generated test module is not valid JavaScript';
  if (error.code === 'MODULE_NOT_FOUND' && !inSolution && !String(error.message).includes("'./solution'"))
    return 'the grading environment has no required module';
  if (error instanceof ReferenceError && !inSolution) return 'the generated test uses an undeclared name';
  return null;
}
function checked(fn) {
  if (fn.length > 1) return fn; // Native callback tests must retain their arity/lifecycle.
  return async (...args) => {
    try { return await fn(...args); }
    catch (error) {
      const why = reason(error);
      if (why) error.message += ' [MINDMORPH_HARNESS: ' + why + ']';
      throw error;
    }
  };
}
const exportedTest = require('node:test');
for (const key of ['test', 'it']) {
  const original = exportedTest[key];
  const wrapper = (...args) => original(...args.map(arg => typeof arg === 'function' ? checked(arg) : arg));
  for (const mode of ['skip', 'todo', 'only'])
    wrapper[mode] = (...args) => original[mode](...args.map(arg => typeof arg === 'function' ? checked(arg) : arg));
  exportedTest[key] = wrapper;
}
try {
  new vm.Script(fs.readFileSync('./test_solution.js', 'utf8'), {filename: 'test_solution.js'});
  const tests = require('./test_solution.js');
  for (const [name, fn] of Object.entries(tests || {}))
    if (name.startsWith('test') && typeof fn === 'function') test(name, checked(fn));
} catch (error) {
  test('collect', checked(() => { throw error; }));
}
'''

_JS_REPORTER_SRC = r'''
const fs = require('node:fs');
module.exports = async function* (events) {
  const result = {version: 1, complete: false, passed: 0, total: 0, failures: [], harness: []};
  const outcomes = [];
  const active = [];
  const containers = new Set();
  const key = data => JSON.stringify([data.file, data.line, data.column, data.nesting, data.name]);
  let summary = false;
  for await (const event of events) {
    const data = event.data || {};
    if (event.type === 'test:start') {
      if (data.nesting > 0 && active[data.nesting - 1]) containers.add(active[data.nesting - 1]);
      active[data.nesting] = key(data);
      active.length = data.nesting + 1;
    }
    if (event.type === 'test:stdout' || event.type === 'test:stderr') yield data.message;
    if (event.type === 'test:pass' || event.type === 'test:fail') outcomes.push({type: event.type, data});
    if (event.type === 'test:plan' && data.nesting === 0) summary = true;
  }
  for (const {type, data} of outcomes) {
    const details = data.details || {};
    const error = details.error || {};
    if (details.type === 'suite' || containers.has(key(data))) continue;
    const fileWrapper = data.name === data.file || /(?:^|[\\/])_runner\.js$/.test(data.name || '');
    if (fileWrapper) {
      if (type === 'test:fail') result.harness.push('The JavaScript test process ended before completing normally.');
      continue;
    }
    if (data.skip || data.todo) continue;
    result.total++;
    if (type === 'test:pass') { result.passed++; continue; }
    const message = data.name + ': ' + String(error.cause?.message || error.message || 'test failed');
    const stack = String(error.cause?.stack || error.stack || '');
    const inSolution = /(?:^|[\\/])solution\.js:\d/.test(stack);
    const harness = message.includes('[MINDMORPH_HARNESS:') ||
      ((error.cause?.code === 'MODULE_NOT_FOUND') && !inSolution && !message.includes("'./solution'")) ||
      ((error.cause?.name === 'ReferenceError') && !inSolution);
    result[harness ? 'harness' : 'failures'].push(message);
  }
  result.complete = summary;
  fs.writeFileSync('_grading_result.json', JSON.stringify(result), 'utf8');
};
'''

_SQL_RUNNER_SRC = r'''
import json, sqlite3
with open('solution.sql', encoding='utf-8') as stream: solution = stream.read()
with open('test_assertions.json', encoding='utf-8') as stream: assertions = json.load(stream)
result = dict(version=1, complete=True, passed=0, total=len(assertions), failures=[], harness=[])
connection = sqlite3.connect(':memory:')
try:
    connection.executescript(solution)
except sqlite3.Error as exc:
    result['failures'].append('Solution SQL error: ' + str(exc))
else:
    for assertion in assertions:
        try:
            actual = [list(row) for row in connection.execute(assertion['query']).fetchall()]
            if actual == assertion['expected']: result['passed'] += 1
            else: result['failures'].append(assertion['label'] + ': expected ' + json.dumps(assertion['expected'], ensure_ascii=False) + ' but got ' + json.dumps(actual, ensure_ascii=False))
        except sqlite3.Error as exc:
            result['failures'].append(assertion['label'] + ': SQL error: ' + str(exc))
connection.close()
with open('_grading_result.json', 'w', encoding='utf-8') as stream: json.dump(result, stream, ensure_ascii=False)
'''


def _apply_resource_limits():  # pragma: no cover - POSIX child
    if resource is not None:
        for limit, value in ((resource.RLIMIT_CPU, 15), (resource.RLIMIT_FSIZE, 16 * 1024 * 1024)):
            try:
                resource.setrlimit(limit, (value, value))
            except (ValueError, OSError):
                pass


def _child_env(tmp):
    env = {key: value for key, value in os.environ.items()
           if not any(marker in key.upper() for marker in _SECRET_MARKERS)}
    env.update(PYTHONPATH=tmp, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8", PYTHONUTF8="1", NODE_PATH=tmp)
    env.pop("NODE_OPTIONS", None)
    return env


def _strip_sandbox_paths(output):
    return _SANDBOX_PATH_RE.sub(lambda match: re.split(r"[/\\]", match.group(0))[-1], output or "")


def _harness_result(reasons, stdout=""):
    return {"harness_error": True, "failures": _bounded_reasons(reasons),
            "passed": 0, "total": 0, "stdout": _strip_sandbox_paths(stdout)[-4000:], "timed_out": False}


def _bounded_reasons(reasons):
    return [_strip_sandbox_paths(str(item))[:2000] for item in reasons[:20]]


def _windows_job(proc):  # pragma: no cover - Windows integration
    """A kill-on-close job cleans descendants even after their parent exits."""
    import ctypes
    from ctypes import wintypes
    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]
    class IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ("ReadOperationCount", "WriteOperationCount",
                    "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
    class Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateJobObjectW(None, None)
    if not handle: return None
    limits = Extended()
    limits.BasicLimitInformation.LimitFlags = 0x2000
    if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(handle, wintypes.HANDLE(int(proc._handle))):
        kernel.CloseHandle(handle)
        return None
    return lambda: kernel.CloseHandle(handle)


def _kill_process_tree(proc):
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        elif proc.poll() is None:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=3)
    except (OSError, subprocess.SubprocessError):
        pass
    if proc.poll() is None:
        try: proc.kill()
        except OSError: pass


def _run(cmd, cwd, env, timeout):
    """Drain into a bounded output tail and terminate descendants on every exit."""
    options = dict(cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    if os.name == "posix":
        options.update(start_new_session=True, preexec_fn=_apply_resource_limits)
    else:
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    proc = subprocess.Popen(cmd, **options)
    job = _windows_job(proc) if os.name == "nt" else None
    chunks = deque()
    output_lock = threading.Lock()
    size = 0
    def drain():
        nonlocal size
        try:
            while True:
                chunk = proc.stdout.read(4096)
                if not chunk: return
                with output_lock:
                    chunks.append(chunk)
                    size += len(chunk)
                    while size > _OUTPUT_BYTES: size -= len(chunks.popleft())
        except (OSError, ValueError): pass
    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    timed_out = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
    finally:
        if job: job()
        _kill_process_tree(proc)
        try: proc.wait(timeout=3)
        except subprocess.TimeoutExpired: pass
        reader.join(timeout=3)
        if not reader.is_alive(): proc.stdout.close()
    with output_lock:
        output = b"".join(chunks).decode("utf-8", errors="replace")
    return output, proc.returncode, timed_out


def _execute(files, command, timeout, javascript=False):
    tmp = tempfile.mkdtemp(prefix="mindmorph_grade_")
    try:
        for filename, source in files.items():
            with open(os.path.join(tmp, filename), "w", encoding="utf-8") as stream: stream.write(source)
        try:
            stdout, returncode, timed_out = _run(command, tmp, _child_env(tmp), timeout)
        except OSError as exc:
            return _harness_result([f"The grading runtime could not start: {exc}"])
        stdout = _strip_sandbox_paths(stdout)
        if timed_out:
            return dict(passed=0, total=0, failures=["Execution timed out (possible infinite loop)."],
                        score=0.0, stdout=stdout[-4000:], timed_out=True)
        try:
            path = os.path.join(tmp, _RESULT_FILE)
            if os.path.getsize(path) > 1024 * 1024: raise ValueError("grading result is oversized")
            with open(path, encoding="utf-8") as stream: result = json.load(stream)
            passed, total = result["passed"], result["total"]
            valid = (result.get("version") == 1 and result.get("complete") is True
                     and type(passed) is int and type(total) is int and 0 <= passed <= total
                     and not (passed == total and result.get("failures"))
                     and all(isinstance(result.get(key), list) and all(isinstance(item, str) for item in result[key])
                             for key in ("failures", "harness")))
            allowed = (0, 1) if javascript and (result.get("failures") or result.get("harness")) else (0,)
            if not valid or returncode not in allowed: raise ValueError("grading process did not complete consistently")
        except (OSError, ValueError, TypeError, KeyError) as exc:
            return _harness_result([f"The grading runner could not complete the tests: {exc}"], stdout)
        if result["harness"]: return _harness_result(result["harness"], stdout)
        if not total: return _harness_result(["No completed executable tests were generated."], stdout)
        if passed < total and not result["failures"]: return _harness_result(["The grading runner returned incomplete test counts."], stdout)
        return dict(passed=passed, total=total, failures=_bounded_reasons(result["failures"]),
                    score=passed / total * 100.0, stdout=stdout[-4000:], timed_out=False)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def execute_tests(solution_code, test_code, timeout=DEFAULT_TIMEOUT_SECONDS):
    if not test_code or not test_code.strip(): return _harness_result(["No unit tests were generated for this exercise."])
    return _execute({"solution.py": solution_code or "", "test_solution.py": test_code, "_runner.py": _RUNNER_SRC},
                    [sys.executable, "_runner.py"], timeout)


def execute_javascript(solution_code, test_code, timeout=DEFAULT_TIMEOUT_SECONDS):
    if not test_code or not test_code.strip(): return _harness_result(["No unit tests were generated for this exercise."])
    node = shutil.which("node")
    if not node: return _harness_result(["Node.js is not installed; cannot grade JavaScript exercises."])
    return _execute({"solution.js": solution_code or "", "test_solution.js": test_code,
                     "_runner.js": _JS_RUNNER_SRC, "_reporter.cjs": _JS_REPORTER_SRC},
                    [node, "--test", "--test-reporter=./_reporter.cjs", "_runner.js"], timeout, javascript=True)


def execute_sql(solution_sql, test_assertions_json, timeout=DEFAULT_TIMEOUT_SECONDS):
    try: assertions = normalize_sql_assertions(test_assertions_json)
    except ValueError as exc: return _harness_result([str(exc)])
    return _execute({"solution.sql": solution_sql or "", "test_assertions.json": json.dumps(assertions, ensure_ascii=False),
                     "_sql_runner.py": _SQL_RUNNER_SRC}, [sys.executable, "_sql_runner.py"], timeout)


_FILE_IO_RE = re.compile(r"\bopen\s*\(|\bread_csv\b|\bread_json\b|\bread_excel\b|\bread_parquet\b|\bnp\.load\b"
                         r"|['\"][^'\"]*\.(?:csv|json|txt|parquet|xlsx|npy)['\"]")
_STUB_SOLUTION = "def __getattr__(name):\n    return lambda *a, **k: None\n"
_STUB_JS_SOLUTION = "module.exports = new Proxy({}, { get: (_, name) => (...args) => undefined });\n"


def check_test_artifact(unit_tests: List[str]) -> Optional[str]:
    src = "\n".join(unit_tests or [])
    match = _FILE_IO_RE.search(src)
    if match: return f"tests read an external file ({match.group(0)!r}); the sandbox has no data files"
    result = execute_tests(_STUB_SOLUTION, src)
    return "; ".join(result["failures"]) if result.get("harness_error") else None


def check_test_artifact_js(unit_tests: List[str]) -> Optional[str]:
    result = execute_javascript(_STUB_JS_SOLUTION, "\n".join(unit_tests or []))
    return "; ".join(result["failures"]) if result.get("harness_error") else None


def check_test_artifact_sql(sql_assertions: List[str]) -> Optional[str]:
    try: normalize_sql_assertions(sql_assertions)
    except ValueError as exc: return str(exc)
    return None


def execute_exercise(language, solution_code, test_code, timeout=DEFAULT_TIMEOUT_SECONDS):
    try: language = normalize_language(language)
    except ValueError as exc: return _harness_result([str(exc)])
    executor = {"python": execute_tests, "javascript": execute_javascript, "sql": execute_sql}[language]
    return executor(solution_code, test_code, timeout)
