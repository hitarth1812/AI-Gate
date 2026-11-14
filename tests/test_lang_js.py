"""JS/TS on the lexer: the same rules as before, but text inside strings, template literals, regex literals and
comments no longer counts as code (and comments are the only place markers count). The pre-existing JS tests in the
other files cover the behaviour that must not change."""
import json

from conftest import find

AWS = "AKIA" + "qwertyuiopasdfgh".upper()                 # split so this file is not itself a finding


def stage(repo, before, after, *args):
    for path, text in before.items():
        repo.write(path, text)
    if before:
        repo.commit("base")
    for path, text in after.items():
        if text is None:
            (repo.path / path).unlink()
        else:
            repo.write(path, text)
    repo.add()
    return repo.check("--offline", *args)


def lines(res, rule):
    return [f["line"] for f in find(res, rule) if not f["allowed"]]


def test_focused_and_skipped_tests_only_count_in_code(repo):
    res = stage(repo, {}, {"a.test.js": "it.only('a', () => {})\nit.skip('b', () => {})\n"
                                        "const s = \"it.only(\"\n// it.skip(\nconst t = `\nit.only(\n`\n"})
    assert lines(res, "tests.focused") == [1] and lines(res, "tests.skip_added") == [2]


def test_commenting_a_test_out_is_now_a_deletion(repo):
    before = "it('a', () => { expect(1).toBe(1) })\nit('b', () => { expect(2).toBe(2) })\n"
    res = stage(repo, {"a.test.js": before}, {"a.test.js": "it('a', () => { expect(1).toBe(1) })\n"
                                                           "// it('b', () => { expect(2).toBe(2) })\n"})
    assert find(res, "tests.deleted") and find(res, "tests.assertions_removed")


def test_assertions_in_a_string_do_not_hide_a_removed_assertion(repo):
    before = "it('a', () => {\n  expect(f()).toBe(1)\n})\n"
    res = stage(repo, {"a.test.js": before}, {"a.test.js": "it('a', () => {\n  const s = 'expect(f()).toBe(1)'\n})\n"})
    assert find(res, "tests.assertions_removed")


def test_suppressions_count_only_in_comments(repo):
    # the markers are split in the source so the repository's own self-scan does not flag these fixtures
    res = stage(repo, {}, {"a.ts": "// @ts-" "ignore\nconst a = 1\nconst s = '// @ts-" "ignore'\nconst t = `// eslint-" "disable-line`\n"
                                   "/* eslint-" "disable */\nconst r = /\\/\\/ @ts-" "ignore/\n"})
    assert lines(res, "suppress.type") == [1] and lines(res, "suppress.blanket") == [5]
    assert not find(res, "suppress.lint")


def test_any_casts_in_strings_and_comments_are_not_casts(repo):
    res = stage(repo, {}, {"a.ts": "const a = x as any\nconst s = 'x as any'\n// y as any\nlet z: any = 1\n"})
    assert lines(res, "suppress.type_cast") == [1, 4]


def test_markers_in_strings_are_not_todos_or_placeholders(repo):
    res = stage(repo, {}, {"a.js": "// TO" "DO: fix\nconst s = '// TO" "DO: no'\n// rest of the " "implementation here\n"
                                   "const t = 'rest of the " "implementation here'\nthrow new Error('not implemented')\n"
                                   "// throw new Error('not implemented')\n"})
    assert lines(res, "code.todo") == [1]
    assert lines(res, "code.placeholder") == [3, 5]


def test_dangerous_calls_inside_strings_comments_and_regexes_are_ignored(repo):
    res = stage(repo, {}, {"a.js": "eval(x)\nconst s = 'eval(x)'\n// eval(x)\nconst r = /eval\\(x\\)/\n"
                                   "console.log('hi')\n// console.log('hi')\nconst t = 'console.log(1)'\n"
                                   "debugger\n// debugger\n"})
    assert lines(res, "security.eval") == [1] and lines(res, "code.console_log") == [5] and lines(res, "code.debug_leftover") == [8]


def test_empty_catch_written_inside_a_string_is_not_swallowing(repo):
    res = stage(repo, {}, {"a.js": "try { f() } catch (e) {}\nconst s = 'try { f() } catch (e) {}'\n"
                                   "// catch (e) {}\n"})
    assert lines(res, "code.swallowed_exception") == [1]


def test_weak_crypto_words_only_in_a_comment_do_not_fire(repo):
    res = stage(repo, {}, {"a.js": "const token = Math.random()\nconst n = Math.random() // password token\n"})
    assert lines(res, "security.weak_crypto") == [1]


def test_tls_verification_in_a_comment_is_not_disabled(repo):
    res = stage(repo, {}, {"a.js": "const a = { rejectUnauthorized: false }\n// rejectUnauthorized: false\n"
                                   "const s = 'rejectUnauthorized: false'\n"})
    assert lines(res, "security.verification_disabled") == [1]


def test_division_and_regex_do_not_break_the_lexer(repo):
    res = stage(repo, {}, {"a.js": "const a = b / c / d\nconst r = /[\"'`]/g\neval(x)\nconst s = 'ok'\n"})
    assert lines(res, "security.eval") == [3]


def test_template_literal_interpolation_is_code(repo):
    res = stage(repo, {}, {"a.js": "const a = `x ${eval(y)} z`\nconst b = `eval(y) ${1}`\n"})
    assert lines(res, "security.eval") == [1]


def test_a_file_the_lexer_cannot_read_falls_back_to_line_checks_with_a_note(repo):
    text = "const a = `never closed ${ \n// TODO: fix\nconst k = \"" + AWS + "\"\neval(x)\n"
    repo.write("a.js", text)
    repo.add()
    proc = repo.run("check", "--format", "json", "--offline", "-v")
    res = json.loads(proc.stdout)
    assert find(res, "secrets.hardcoded") and find(res, "code.todo") and find(res, "security.eval")
    assert any(n.startswith("lang.fallback: a.js") for n in res["notes"])
    assert not any("lang.fallback" in n for n in repo.check("--offline")["notes"])


def test_functions_in_js_use_brace_structure_for_size_and_loops(repo):
    body = "".join(f"  x = {i}\n" for i in range(70))
    res = stage(repo, {}, {"a.ts": f"function big(a: number) {{\n{body}  return a\n}}\n"
                                   "const f = (a) => {\n  for (const x of a) {\n    for (const y of x) {\n      for (const z of y) {\n"
                                   "        use(z)\n      }\n    }\n  }\n}\n",
                           ".aigate/lite.toml": '[rules]\n"size.*" = "warn"\n"perf.*" = "warn"\n'})   # off for people unless named
    assert find(res, "size.function_length") and find(res, "perf.nested_loops")
    quiet = stage(repo, {}, {"a.ts": "const s = '} } for (a of b) { for (c of d) { for (e of f) {'\n"
                                     "function ok() {\n  // for (a of b) { for (c of d) { for (e of f) { x }}}\n  return 1\n}\n"})
    assert not find(quiet, "perf.nested_loops") and not find(quiet, "size.function_length")


def test_class_methods_and_control_keywords_are_not_confused(repo):
    text = ("class A {\n  m(x) {\n    if (x) {\n      return 1\n    }\n    while (x) {\n      x--\n    }\n    return 2\n  }\n}\n")
    assert not find(stage(repo, {}, {"a.ts": text}), "gate.error")


def test_crlf_js_files(repo):
    (repo.path / "a.test.js").write_bytes(b"it.only('a', () => {})\r\n// it.only('b', () => {})\r\n")
    repo.add()
    assert lines(repo.check("--offline"), "tests.focused") == [1]


def test_vue_and_svelte_files_stay_on_line_based_checks(repo):
    res = stage(repo, {}, {"a.vue": "<template><p>don't</p></template>\n<script>\neval(x)\n</script>\n"})
    assert lines(res, "security.eval") == [3]


# ---------------------------------------------------------------- agent hardening and explain labels

def test_go_work_is_forbidden_for_agents_and_go_mod_is_sensitive(repo):
    repo.write("go.mod", "module x.io/m\n\ngo 1.21\n")
    repo.commit("base")
    repo.write("go.work", "go 1.21\n\nuse .\n")
    repo.add()
    agent = repo.check("--offline", env={"CLAUDECODE": "1"})
    assert [f["path"] for f in find(agent, "scope.forbidden_path")] == ["go.work"]
    assert not find(repo.check("--offline"), "scope.forbidden_path")
    from conftest import aigate
    assert {"go.mod", "go.sum", "go.work"} <= set(aigate.DEFAULT_CONFIG["paths"]["sensitive"])


def test_explain_labels_the_brace_structure_rules_as_heuristic(repo):
    for rule in ("size.function_length", "size.complexity", "perf.nested_loops"):
        assert "estimated" in repo.run("explain", rule).stdout
    assert "estimated" not in repo.run("explain", "code.todo").stdout


# ---------------------------------------------------------------- review fixes

def test_an_indented_blanket_eslint_disable_is_still_a_suppression(repo):
    res = stage(repo, {}, {"a.js": "function f() {\n  // eslint-disable\n  return 1\n}\n"})
    assert lines(res, "suppress.blanket") == [2]


def test_a_multiline_test_header_still_counts_as_a_test(repo):
    before = "it(\n  'a long test name that prettier wrapped',\n  () => { expect(1).toBe(1) })\n"
    res = stage(repo, {"a.test.js": before}, {"a.test.js": "// nothing left\n"})
    assert find(res, "tests.deleted")


def test_an_empty_catch_with_a_comment_is_an_intentional_ignore(repo):
    res = stage(repo, {}, {"a.js": "try { f() } catch (e) { /* ignore */ }\ntry { g() } catch (e) {}\n"
                                   "try { h() } catch (e) {\n  // ignored on purpose\n}\n"})
    assert lines(res, "code.swallowed_exception") == [2]


def test_postfix_increment_before_a_division_is_not_a_regex(repo):
    res = stage(repo, {}, {"a.js": "n++ / 2; eval(x); y = b / 3\nobj.in / z; eval(w)\n"})
    assert lines(res, "security.eval") == [1, 2]
