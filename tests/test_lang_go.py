"""Go: tests, unsafe patterns, placeholders, suppressions and structure, through the whole gate. Every rule has a
positive case, a negative case and the same text inside a string or a comment (which must not fire)."""
import json

import pytest

from conftest import find, rules

AWS = "AKIA" + "QWERTYUIOPASDFGH"                 # split so this file is not itself a finding


def stage(repo, before, after, *args):
    """Commit `before` ({path: text}), then stage `after` ({path: text or None to delete}) and run the gate."""
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


def go(body, pkg="package app\n\n"):
    return pkg + body


def hits(res, rule):
    return [f["line"] for f in find(res, rule) if not f["allowed"]]


# ---------------------------------------------------------------- tests

def test_a_skipped_go_test_is_flagged_but_not_in_strings_or_comments(repo):
    base = go("func TestA(t *testing.T) {\n\tt.Errorf(\"x\")\n}\n")
    res = stage(repo, {"a_test.go": base}, {"a_test.go": go(
        "func TestA(t *testing.T) {\n\tt.Errorf(\"x\")\n\tt.Skip(\"flaky\")\n}\n")})
    assert hits(res, "tests.skip_added") == [5]
    quiet = stage(repo, {}, {"a_test.go": go(
        "func TestA(t *testing.T) {\n\tt.Errorf(\"x\")\n\tmsg := \"t.Skip(\"\n\t_ = msg\n\t// t.Skip(\n}\n")})
    assert not hits(quiet, "tests.skip_added")


def test_skipnow_and_skipf_and_suite_skips(repo):
    res = stage(repo, {"a_test.go": go("func TestA(t *testing.T) { t.Errorf(\"x\") }\n")}, {"a_test.go": go(
        "func TestA(t *testing.T) {\n\tt.Errorf(\"x\")\n\tt.SkipNow()\n\tb.Skipf(\"n\")\n\ts.T().Skip()\n}\n")})
    assert hits(res, "tests.skip_added") == [5, 6, 7]


def test_removed_go_assertions_are_flagged(repo):
    before = go("func TestA(t *testing.T) {\n\tif f() != 1 {\n\t\tt.Errorf(\"bad\")\n\t}\n\tassert.Equal(t, 1, g())\n}\n")
    res = stage(repo, {"a_test.go": before}, {"a_test.go": go("func TestA(t *testing.T) {\n\tf()\n}\n")})
    assert find(res, "tests.assertions_removed")
    kept = stage(repo, {}, {"a_test.go": go(
        "func TestA(t *testing.T) {\n\tif f() != 2 {\n\t\tt.Errorf(\"bad\")\n\t}\n\tassert.Equal(t, 2, g())\n}\n")})
    assert not find(kept, "tests.assertions_removed")


def test_assertion_words_in_strings_and_comments_do_not_count(repo):
    before = go("func TestA(t *testing.T) {\n\t// t.Errorf(\"x\")\n\ts := \"assert.Equal(\"\n\t_ = s\n}\n")
    res = stage(repo, {"a_test.go": before}, {"a_test.go": go("func TestA(t *testing.T) {\n}\n")})
    assert not find(res, "tests.assertions_removed")


def test_deleted_go_tests_and_test_files(repo):
    base = go("func TestA(t *testing.T) { t.Fatal(1) }\nfunc TestB(t *testing.T) { t.Fatal(2) }\n")
    res = stage(repo, {"a_test.go": base}, {"a_test.go": go("func TestA(t *testing.T) { t.Fatal(1) }\n")})
    assert find(res, "tests.deleted") and "TestB" in find(res, "tests.deleted")[0]["message"]
    gone = stage(repo, {}, {"a_test.go": None})
    assert find(gone, "tests.deleted")


def test_moving_or_renaming_go_tests_is_not_a_deletion(repo):
    base = go("func TestA(t *testing.T) { t.Fatal(1) }\nfunc TestB(t *testing.T) { t.Fatal(2) }\n")
    moved = stage(repo, {"a_test.go": base}, {"a_test.go": None, "b_test.go": base})
    assert not find(moved, "tests.deleted")
    repo.commit("moved")
    renamed = stage(repo, {}, {"b_test.go": go("func TestA(t *testing.T) { t.Fatal(1) }\nfunc TestC(t *testing.T) { t.Fatal(2) }\n")})
    assert not find(renamed, "tests.deleted")


def test_a_commented_out_test_function_is_a_deletion(repo):
    base = go("func TestA(t *testing.T) { t.Fatal(1) }\n")
    res = stage(repo, {"a_test.go": base}, {"a_test.go": go("// func TestA(t *testing.T) { t.Fatal(1) }\n")})
    assert find(res, "tests.deleted")


# ---------------------------------------------------------------- unsafe patterns

def test_shell_commands_built_at_runtime(repo):
    res = stage(repo, {}, {"a.go": go(
        "func f(cmd string) {\n\texec.Command(\"sh\", \"-c\", cmd).Run()\n\texec.Command(\"bash\", \"-c\", \"ls \"+cmd).Run()\n}\n")})
    assert hits(res, "security.shell") == [4, 5]
    ok = stage(repo, {}, {"a.go": go(
        "func f(cmd string) {\n\texec.Command(\"sh\", \"-c\", \"ls -la\").Run()\n\texec.Command(\"ls\", cmd).Run()\n"
        "\ts := `exec.Command(\"sh\", \"-c\", x)`\n\t_ = s\n\t// exec.Command(\"sh\", \"-c\", x)\n}\n")})
    assert not find(ok, "security.shell")


def test_sprintf_built_sql(repo):
    res = stage(repo, {}, {"a.go": go(
        "func f(db *sql.DB, id string) {\n\tdb.Query(fmt.Sprintf(\"SELECT * FROM t WHERE id=%s\", id))\n"
        "\tdb.ExecContext(ctx, \"DELETE FROM t WHERE id=\"+id)\n}\n")})
    assert hits(res, "security.sql_injection") == [4, 5]
    ok = stage(repo, {}, {"a.go": go(
        "func f(db *sql.DB, id string) {\n\tdb.Query(\"SELECT * FROM t WHERE id=$1\", id)\n"
        "\t// db.Query(fmt.Sprintf(\"SELECT %s\", id))\n\ts := `db.Exec(\"DELETE \" + id)`\n\t_ = s\n}\n")})
    assert not find(ok, "security.sql_injection")


def test_math_rand_for_secrets_but_not_crypto_rand_or_plain_numbers(repo):
    bad = go("import \"math/rand\"\n\nfunc f() int {\n\ttoken := rand.Int()\n\treturn token\n}\n", pkg="package app\n\n")
    res = stage(repo, {}, {"a.go": bad})
    assert hits(res, "security.weak_crypto") == [6]
    good = go("import \"crypto/rand\"\n\nfunc f() {\n\tb := make([]byte, 8)\n\trand.Read(b)\n\ttoken := b\n\t_ = token\n}\n")
    assert not find(stage(repo, {}, {"a.go": good}), "security.weak_crypto")
    dice = go("import \"math/rand\"\n\nfunc roll() int {\n\treturn rand.Intn(6)\n}\n")
    assert not find(stage(repo, {}, {"a.go": dice}), "security.weak_crypto")
    comment = go("import \"math/rand\"\n\nfunc f() int {\n\t// token := rand.Int()\n\treturn rand.Intn(6)\n}\n")
    assert not find(stage(repo, {}, {"a.go": comment}), "security.weak_crypto")


def test_dropped_errors(repo):
    res = stage(repo, {}, {"a.go": go(
        "func f() {\n\t_, err := g()\n\t_ = err\n\tif err != nil {}\n\tif err != nil {\n\t}\n}\n")})
    assert hits(res, "code.swallowed_exception") == [5, 6, 7]
    ok = stage(repo, {}, {"a.go": go(
        "func f() error {\n\t_, err := g()\n\tif err != nil {\n\t\treturn err\n\t}\n\ts := \"_ = err\"\n\t_ = s\n"
        "\t// if err != nil {}\n\treturn nil\n}\n")})
    assert not find(ok, "code.swallowed_exception")


def test_tls_verification_switched_off(repo):
    res = stage(repo, {}, {"a.go": go(
        "var c = &tls.Config{InsecureSkipVerify: true}\nfunc f() {\n\tc.InsecureSkipVerify = true\n}\n")})
    assert hits(res, "security.verification_disabled") == [3, 5]
    ok = stage(repo, {}, {"a.go": go(
        "var c = &tls.Config{InsecureSkipVerify: false}\n// InsecureSkipVerify: true is dangerous\nvar s = \"InsecureSkipVerify = true\"\n")})
    assert not find(ok, "security.verification_disabled")


# ---------------------------------------------------------------- placeholders, TODOs, suppressions, generate

def test_not_implemented_panics_are_placeholders(repo):
    res = stage(repo, {}, {"a.go": go(
        "func f() {\n\tpanic(\"TODO\")\n}\nfunc g() {\n\tpanic(\"not implemented\")\n}\n")})
    assert hits(res, "code.placeholder") == [4, 7]
    ok = stage(repo, {}, {"a.go": go(
        "func f() {\n\tpanic(\"unreachable state\")\n\t// panic(\"TODO\")\n\ts := `panic(\"TODO\")`\n\t_ = s\n}\n")})
    assert not find(ok, "code.placeholder")


def test_todo_comments_only_count_in_comments(repo):
    res = stage(repo, {}, {"a.go": go("// TODO: finish\nvar s = \"// TODO: not a comment\"\nvar t = `\n// TODO in a raw string\n`\n")})
    assert hits(res, "code.todo") == [3]


def test_nolint_and_lint_ignore_are_suppressions_gosec_is_a_security_one(repo):
    res = stage(repo, {}, {"a.go": go(
        "func f() {\n\tx := 1 //nolint\n\ty := 2 // nolint:errcheck\n\t//lint:ignore SA1000 reason\n"
        "\tz := 3 //nol" "int:gosec\n\t_, _, _ = x, y, z\n}\n")})            # split: the repo scans itself
    assert hits(res, "suppress.lint") == [4, 5, 6]
    assert hits(res, "suppress.security") == [7]
    ok = stage(repo, {}, {"a.go": go("var s = \"//nolint\"\nvar r = `//nol" "int:gosec`\n")})
    assert not find(ok, "suppress.lint") and not find(ok, "suppress.security")


def test_a_new_go_generate_directive_warns(repo):
    res = stage(repo, {"a.go": go("var x = 1\n")}, {"a.go": go("//go:generate stringer -type=X\nvar x = 1\n")})
    gen = [f for f in find(res, "code.generate_directive")]
    assert gen and gen[0]["action"] == "warn"
    again = stage(repo, {}, {"a.go": go("var s = \"//go:generate x\"\n// see //go:generate docs\nvar x = 1\n")})
    assert not find(again, "code.generate_directive")


# ---------------------------------------------------------------- structure

def long_func(n):
    return "func big() {\n" + "\tx := 1\n" * n + "\t_ = x\n}\n"


CORE_ON = '[rules]\n"size.*" = "warn"\n"perf.*" = "warn"\n"code.duplicate_block" = "warn"\n'   # these are off for people unless named


def test_long_functions_deep_loops_and_branchy_functions_warn(repo):
    res = stage(repo, {}, {"a.go": go(long_func(70)), ".aigate/lite.toml": CORE_ON})
    assert find(res, "size.function_length")
    loops = go("func f(a [][][]int) {\n\tfor _, x := range a {\n\t\tfor _, y := range x {\n\t\t\tfor _, z := range y {\n\t\t\t\t_ = z\n\t\t\t}\n\t\t}\n\t}\n}\n")
    assert find(stage(repo, {}, {"a.go": loops}), "perf.nested_loops")
    branchy = go("func f(a int) int {\n" + "".join(f"\tif a == {i} {{\n\t\treturn {i}\n\t}}\n" for i in range(12)) + "\treturn 0\n}\n")
    assert find(stage(repo, {}, {"a.go": branchy}), "size.complexity")


def test_structure_ignores_braces_and_keywords_in_strings_and_comments(repo):
    text = go("func f() {\n\ts := \"} } for for for if if if\"\n\t// for for for { { {\n\t_ = s\n}\n")
    res = stage(repo, {}, {"a.go": text})
    assert not find(res, "perf.nested_loops") and not find(res, "size.function_length") and not find(res, "size.complexity")


def test_small_edits_to_an_already_long_function_are_not_new_findings(repo):
    base = go(long_func(70))
    res = stage(repo, {"a.go": base}, {"a.go": go(long_func(70)).replace("_ = x", "_ = x // note"), ".aigate/lite.toml": CORE_ON})
    assert not find(res, "size.function_length")
    grown = stage(repo, {}, {"a.go": go(long_func(80))})
    assert find(grown, "size.function_length")


def test_closures_interfaces_and_generics_are_handled(repo):
    text = go("type I interface{ M() }\n\nfunc g[T any](x T) interface{} {\n\tf := func(a int) int { return a }\n\t_ = f\n\treturn x\n}\n\n"
              "func (s *S) Method(a, b int) (int, error) {\n\treturn a + b, nil\n}\n")
    res = stage(repo, {}, {"a.go": text})
    assert not find(res, "gate.error")


# ---------------------------------------------------------------- fallback, CRLF, odd paths

def test_a_file_the_lexer_cannot_read_still_gets_secrets_and_line_checks_and_a_note(repo):
    broken = go("var raw = `never closed\n// TODO: fix\nvar k = \"" + AWS + "\"\nfunc f() { panic(\"TODO\") }\n")
    res = repo_check_verbose(repo, {"a.go": broken})
    assert find(res, "secrets.hardcoded") and find(res, "code.todo")
    assert any(n.startswith("lang.fallback: a.go") for n in res["notes"])
    quiet = stage(repo, {}, {"a.go": broken})
    assert not any("lang.fallback" in n for n in quiet["notes"])


def repo_check_verbose(repo, files):
    for path, text in files.items():
        repo.write(path, text)
    repo.add()
    proc = repo.run("check", "--format", "json", "--offline", "-v")
    assert proc.returncode in (0, 1), proc.stderr
    return json.loads(proc.stdout)


def test_crlf_go_files_are_lexed_correctly(repo):
    text = "package app\r\n\r\nfunc TestA(t *testing.T) {\r\n\tt.Skip(\"x\")\r\n\t// t.Skip(\"y\")\r\n}\r\n"
    for path, body in (("a_test.go", text),):
        (repo.path / path).write_bytes(body.encode())
    repo.add()
    res = repo.check("--offline")
    assert hits(res, "tests.skip_added") == [4]


def test_go_files_under_paths_with_spaces_and_unicode(repo):
    res = stage(repo, {}, {"my pkg/ü dir/a_test.go": go("func TestA(t *testing.T) {\n\tt.Skip(\"x\")\n}\n")})
    assert hits(res, "tests.skip_added") == [4]
    assert rules(res)


@pytest.mark.parametrize("name", ["vendor/x/a_test.go", "node_modules/x/a.go"])
def test_ignored_paths_are_not_checked(repo, name):
    res = stage(repo, {}, {name: go("func TestA(t *testing.T) {\n\tt.Skip(\"x\")\n\tpanic(\"TODO\")\n}\n")})
    assert not find(res, "tests.skip_added") and not find(res, "code.placeholder")


# ---------------------------------------------------------------- review fixes

def test_math_rand_only_counts_when_the_file_does_not_also_use_crypto_rand(repo):
    mixed = go('import (\n\tcrand "crypto/rand"\n\tmrand "math/rand"\n)\n\nfunc f() {\n\ttoken := make([]byte, 8)\n\tcrand.Read(token)\n}\n')
    assert not find(stage(repo, {}, {"a.go": mixed}), "security.weak_crypto")


def test_sql_concatenation_needs_a_variable_operand(repo):
    ok = go('func f(db *sql.DB) {\n\tdb.Query("select a " + "from b")\n}\n')
    assert not find(stage(repo, {}, {"a.go": ok}), "security.sql_injection")
    bad = go('func f(db *sql.DB, id string) {\n\tdb.Query("select \\"x\\" from t where id = " + id)\n}\n')
    assert find(stage(repo, {}, {"a.go": bad}), "security.sql_injection")


def test_assert_new_is_not_an_assertion_and_test_main_is_not_a_test(repo):
    before = go("func TestMain(m *testing.M) {\n\tos.Exit(m.Run())\n}\nfunc TestHelperThing(t *testing.T) {\n\ta := assert.New(t)\n\t_ = a\n}\n")
    res = stage(repo, {"a_test.go": before}, {"a_test.go": go("func TestHelperThing(t *testing.T) {\n}\n")})
    assert not find(res, "tests.deleted") and not find(res, "tests.assertions_removed")


def test_a_same_named_test_in_another_package_does_not_hide_a_deletion(repo):
    base = go("func TestA(t *testing.T) { t.Fatal(1) }\nfunc TestB(t *testing.T) { t.Fatal(2) }\n")
    res = stage(repo, {"p1/a_test.go": base}, {"p1/a_test.go": go("func TestA(t *testing.T) { t.Fatal(1) }\n"),
                                              "p2/other_test.go": go("func TestB(t *testing.T) { t.Fatal(2) }\n")})
    assert find(res, "tests.deleted")


def test_testify_suite_methods_are_tests(repo):
    base = go("func (s *Suite) TestA() { s.Equal(1, 1) }\nfunc (s *Suite) TestB() { s.Equal(2, 2) }\n")
    res = stage(repo, {"a_test.go": base}, {"a_test.go": go("func (s *Suite) TestA() { s.Equal(1, 1) }\n")})
    assert find(res, "tests.deleted") and "TestB" in find(res, "tests.deleted")[0]["message"]


def test_structure_analysis_is_skipped_with_a_note_for_huge_files(repo):
    text = go(long_func(70)) + "// " + "x" * 520_000 + "\n"
    res = repo_check_verbose(repo, {"a.go": text})
    assert not find(res, "size.function_length")
    assert any("a.go" in n and "size and loop checks skipped" in n for n in res["notes"])
