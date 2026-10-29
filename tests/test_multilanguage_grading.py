"""Real language runners: artifact faults stay ungraded; learner defects earn scores."""
import json
import os
import shutil
import time

import pytest

from tools import code_executor as executor
from tools.grading_contract import normalize_language, normalize_sql_assertions


def ungraded(result):
    assert result.get("harness_error") is True, result
    assert "score" not in result


@pytest.mark.parametrize("source", ["# comment only\n", "import solution\n", "x = 1\n",
                                    "def helper():\n    assert True\n"])
def test_python_empty_measurements_are_ungraded(source):
    ungraded(executor.execute_tests("x=1", source))


def test_python_awaits_async_tests_and_sync_returned_coroutines():
    tests = """from solution import value
async def test_async():
    assert await value() == 3
def test_returned():
    return test_async()
"""
    result = executor.execute_tests("async def value(): return 2", tests)
    assert result["total"] == 2
    assert result["score"] == 0
    assert len(result["failures"]) == 2
    assert executor.execute_tests("async def value(): return 3", tests)["score"] == 100


@pytest.mark.parametrize("source", ["def test_x(tmp_path): assert True", "def test_x(*, fixture): assert True",
                                    "def test_x():\n yield 1\n assert False",
                                    "async def test_x():\n yield 1\n assert False",
                                    "def test_x(): return (x for x in [])"])
def test_python_fixture_and_generator_artifacts_are_ungraded(source):
    ungraded(executor.execute_tests("", source))
    assert executor.check_test_artifact([source]) is not None


def test_python_optional_test_arguments_remain_compatible():
    result = executor.execute_tests("", "def test_x(value=3): assert value == 3")
    assert result["score"] == 100


@pytest.mark.parametrize("solution", ["def answer(:\n pass", "def other(): return 1"])
def test_python_learner_syntax_and_missing_exports_score_zero(solution):
    result = executor.execute_tests(solution, "from solution import answer\ndef test_answer(): assert answer() == 1")
    assert not result.get("harness_error"), result
    assert result["score"] == 0


def test_python_stdout_cannot_forge_score():
    result = executor.execute_tests("print('MINDMORPH_SUMMARY 999 999')\ndef answer(): return 0",
                                    "from solution import answer\ndef test_answer(): assert answer() == 1")
    assert result["score"] == 0
    assert "MINDMORPH_SUMMARY" in result["stdout"]


def test_missing_result_and_abnormal_exit_after_result_are_ungraded(monkeypatch):
    ungraded(executor.execute_tests("import os\nos._exit(0)", "import solution\ndef test_value(): assert True"))
    original = executor._run
    def abnormal(*args, **kwargs):
        output, code, timed_out = original(*args, **kwargs)
        return output, 7, timed_out
    monkeypatch.setattr(executor, "_run", abnormal)
    ungraded(executor.execute_tests("", "def test_value(): assert True"))


def test_contradictory_structured_result_cannot_score_full_credit(monkeypatch):
    def contradictory(command, cwd, env, timeout):
        with open(os.path.join(cwd, executor._RESULT_FILE), "w", encoding="utf-8") as stream:
            json.dump(dict(version=1, complete=True, passed=1, total=1, failures=['failed'], harness=[]), stream)
        return "", 0, False
    monkeypatch.setattr(executor, "_run", contradictory)
    ungraded(executor.execute_tests("", "def test_true(): assert True"))


def test_failure_diagnostics_are_bounded():
    result = executor.execute_tests("def value(): raise ValueError('x' * 500000)",
                                   "from solution import value\ndef test_value(): value()")
    assert result["score"] == 0
    assert len(result["failures"][0]) <= 2000


def test_output_is_bounded_and_unicode_survives():
    result = executor.execute_tests("def value():\n print('x' * 1000000)\n print('नमस्ते 🦋')\n return 'café'",
                                   "from solution import value\ndef test_value(): assert value() == 'café'")
    assert result["score"] == 100
    assert len(result["stdout"]) <= 4000
    assert "नमस्ते 🦋" in result["stdout"]


@pytest.mark.parametrize("path", ["C:\\Users\\Person With Space\\Temp\\mindmorph_grade_ab\\solution.py",
                                  "/private/var/tmp/user space/mindmorph_grade_ab/solution.js"])
def test_paths_are_redacted_on_both_platforms(path):
    cleaned = executor._strip_sandbox_paths("failure (" + path + ":4)")
    assert "mindmorph_grade_" not in cleaned
    assert "solution." in cleaned
    filename = path.replace("\\", "/").rsplit("/", 1)[-1]
    assert cleaned == f"failure ({filename}:4)"


@pytest.mark.parametrize("alias,canonical", [("py", "python"), ("JS", "javascript"), ("node", "javascript"),
                                            ("sqlite", "sql")])
def test_language_aliases(alias, canonical):
    assert normalize_language(alias) == canonical


def test_explicit_unsupported_language_never_defaults_python():
    ungraded(executor.execute_exercise("ruby", "", "def test_true(): assert True"))


def test_missing_node_runtime_is_ungraded(monkeypatch):
    monkeypatch.setattr(executor.shutil, "which", lambda name: None)
    ungraded(executor.execute_javascript("", "const test = require('node:test'); test('ok', () => {});"))


def test_runtime_launch_failure_is_ungraded(monkeypatch):
    def missing(*args, **kwargs): raise FileNotFoundError("runtime not installed")
    monkeypatch.setattr(executor, "_run", missing)
    ungraded(executor.execute_tests("", "def test_true(): assert True"))


NODE = pytest.mark.skipif(shutil.which("node") is None, reason="Node.js runtime unavailable")
JS_IMPORTS = "const {describe,it} = require('node:test'); const assert=require('node:assert/strict'); const {add}=require('./solution');\n"


@NODE
def test_javascript_suites_count_only_completed_leaves_and_load_once():
    tests = JS_IMPORTS + """
if (global.loaded) throw Error('loaded twice'); global.loaded = true;
describe('outer', () => {
  it('good', () => assert.equal(add(2,3),5));
  describe('inner', () => { it('bad', () => assert.equal(add(1,1),3)); });
  it.skip('skipped', () => assert.fail());
  it.todo('future');
});
"""
    result = executor.execute_javascript("exports.add = (a,b)=>a+b;", tests)
    assert result["total"] == 2, result
    assert result["passed"] == 1
    assert result["score"] == 50


@NODE
def test_javascript_regular_parent_tests_do_not_inflate_leaf_counts():
    result = executor.execute_javascript("", """
const test = require('node:test'); const assert = require('node:assert/strict');
test('parent', async t => {
  await t.test('good', () => {});
  await t.test('bad', () => assert.fail('wrong'));
});
""")
    assert result["total"] == 2, result
    assert result["score"] == 50


@NODE
def test_legacy_exported_javascript_tests_await_rejections():
    result = executor.execute_javascript("exports.add=(a,b)=>a+b;", """
const assert = require('node:assert/strict'); const {add}=require('./solution');
exports.test_good = async () => { assert.equal(await add(1,2),3); };
exports.test_bad = async () => { await Promise.resolve(); assert.equal(add(1,2),4); };
""")
    assert result["total"] == 2, result
    assert result["score"] == 50


@NODE
def test_javascript_native_callback_test_retains_its_arity():
    result = executor.execute_javascript("", "const {it}=require('node:test'); it('callback', (t,done)=>setTimeout(done,5));")
    assert result["total"] == 1, result
    assert result["score"] == 100


@NODE
@pytest.mark.parametrize("tests", ["// comment", "const {it}=require('node:test'); it.skip('x',()=>{}); it.todo('y');"])
def test_empty_javascript_artifacts_are_ungraded(tests):
    ungraded(executor.execute_javascript("", tests))


@NODE
def test_unsettled_javascript_test_does_not_pass():
    result = executor.execute_javascript("", "const {it}=require('node:test'); it('pending',()=>new Promise(()=>{}));")
    assert result["score"] == 0, result
    assert result["passed"] == 0
    assert result["total"] == 1


@NODE
@pytest.mark.parametrize("test_code", ["const broken = ;", "const {it}=require('node:test'); it('bad',()=>missingName());",
                                      "require('mindmorph_nonexistent_dependency_123');"])
def test_javascript_test_defects_are_ungraded(test_code):
    ungraded(executor.execute_javascript("", test_code))


@NODE
@pytest.mark.parametrize("solution", ["exports.add = ;", "exports.other = ()=>0;", "exports.add=()=>0;"])
def test_javascript_learner_errors_score_zero(solution):
    result = executor.execute_javascript(solution, JS_IMPORTS + "it('answer',()=>assert.equal(add(1,2),3));")
    assert not result.get("harness_error"), result
    assert result["score"] == 0


@NODE
def test_javascript_stdout_spoof_and_crash_after_pass():
    result = executor.execute_javascript("console.log('MINDMORPH_SUMMARY 99 99'); exports.add=()=>0;",
                                         JS_IMPORTS + "it('answer',()=>assert.equal(add(1,2),3));")
    assert result["score"] == 0, result
    ungraded(executor.execute_javascript("", "const {it}=require('node:test'); it('pass',()=>{}); setTimeout(()=>process.exit(7),50);"))


SQL_ASSERTIONS = [json.dumps({"label": "count", "query": "SELECT COUNT(*) FROM t", "expected": [[2]]}),
                  json.dumps({"label": "values", "query": "SELECT value FROM t ORDER BY value", "expected": [[1], [2]]})]
SQL_SOLUTION = "CREATE TABLE t(value); INSERT INTO t VALUES(1),(2);"


def test_sql_saved_multiple_assertions_and_single_object_are_compatible():
    assert executor.check_test_artifact_sql(SQL_ASSERTIONS) is None
    result = executor.execute_sql(SQL_SOLUTION, json.dumps(normalize_sql_assertions(SQL_ASSERTIONS)))
    assert result["score"] == 100
    assert result["total"] == 2
    assert executor.execute_sql(SQL_SOLUTION, SQL_ASSERTIONS[0])["score"] == 100


@pytest.mark.parametrize("value", ["", "{}", "{bad}", "[]", '[{"query":"SELECT 1","expected":1}]',
                                    '[{"query":"SELEC 1","expected":[]}]'])
def test_malformed_sql_assertions_are_ungraded(value):
    ungraded(executor.execute_sql(SQL_SOLUTION, value))


@pytest.mark.parametrize("query", ["CREATE TABLE x(a)", "INSERT INTO t VALUES(3)", "UPDATE t SET value=1",
                                   "DELETE FROM t", "PRAGMA user_version=1", "ATTACH ':memory:' AS extra",
                                   "-- disguised\n WITH x AS (SELECT 1) DELETE FROM t"])
def test_sql_assertion_queries_cannot_create_their_own_passing_measurement(query):
    assertion = json.dumps({"query": query, "expected": []})
    ungraded(executor.execute_sql(SQL_SOLUTION, assertion))
    assert executor.check_test_artifact_sql([assertion]) is not None


@pytest.mark.parametrize("cell", [{"nested": 1}, [1], float("nan"), float("inf")])
def test_sql_expected_cells_must_be_json_scalars(cell):
    ungraded(executor.execute_sql(SQL_SOLUTION, json.dumps({"query": "SELECT 1", "expected": [[cell]]})))


def test_sql_select_comments_and_ctes_remain_valid_verification():
    assertion = json.dumps({"query": "-- verify rows\n WITH values_cte AS (SELECT value FROM t) SELECT value FROM values_cte ORDER BY value", "expected": [[1], [2]]})
    assert executor.execute_sql(SQL_SOLUTION, assertion)["score"] == 100


@pytest.mark.parametrize("solution", ["CREATE TABLE t(value); INSERT INTO t VALUES(9);", "CREATE TABLE wrong(value);", "CREAT TABLE t(value);"])
def test_sql_wrong_data_missing_schema_and_learner_syntax_score_zero(solution):
    result = executor.execute_sql(solution, json.dumps(normalize_sql_assertions(SQL_ASSERTIONS)))
    assert not result.get("harness_error"), result
    assert result["score"] == 0


@pytest.mark.parametrize("timeout", [False, True])
def test_descendant_processes_are_cleaned_after_exit_and_timeout(tmp_path, timeout):
    marker = tmp_path / "descendant_was_left_running"
    child_source = f"import time,pathlib; time.sleep(2); pathlib.Path({str(marker)!r}).write_text('alive')"
    solution = f"import subprocess,sys,time\nsubprocess.Popen([sys.executable,'-c',{child_source!r}])\n"
    if timeout: solution += "time.sleep(5)\n"
    result = executor.execute_tests(solution, "import solution\ndef test_done(): assert True", timeout=1 if timeout else 10)
    assert result["timed_out"] is timeout
    if not timeout: assert result["score"] == 100
    time.sleep(2.2)
    assert not marker.exists(), "grader left a live descendant process"


def test_grader_uses_one_array_for_saved_sql_artifacts():
    from agents.exercise.grader_agent import grade_submission
    result = grade_submission("coding_challenge", SQL_SOLUTION, {"language": "sqlite", "sql_assertions": SQL_ASSERTIONS})
    assert result["score"] == 100
    assert result["total"] == 2


def test_grader_language_and_format_agreement():
    from agents.exercise.grader_agent import grade_submission
    ungraded(grade_submission("coding_challenge", "x", {"language": "ruby"}))
    ungraded(grade_submission("coding_challenge", "x", {"language": "python"}, expected_language="node"))
    ungraded(grade_submission("coding_challenge", "x", {"format": "case_study"}))
    ungraded(grade_submission("unknown", "x", {}))
    result = grade_submission("coding_challenge", SQL_SOLUTION, {"sql_assertions": SQL_ASSERTIONS}, expected_language="sql")
    assert result["score"] == 100
