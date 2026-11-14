"""0.5.3 round 2 (Part 3, stable contract): `--format json` carries `schema_version` (an integer, the version of the document
layout) next to the older `schema` (same value, kept so nothing that reads it breaks). Rule ids are frozen: the list below is the
set shipped in 0.5.2 and a rule may be added but never renamed or removed without a deprecation (see docs/CONTRACT.md)."""
import json

from conftest import aigate


def test_check_json_has_schema_version_equal_to_schema(repo):
    repo.write("a.py", "x = 1\n")
    repo.add()
    data = json.loads(repo.run("check", "--offline", "--format", "json").stdout)
    assert isinstance(data["schema_version"], int) and data["schema_version"] == data["schema"] == 1


def test_review_json_has_schema_version_equal_to_schema(repo):
    repo.write("a.py", "x = 1\n")
    repo.add()
    data = json.loads(repo.run("review", "--offline", "--format", "json").stdout)
    assert data["schema_version"] == data["schema"] == aigate.REVIEW_SCHEMA


def test_the_empty_stage_report_has_it_too(repo):
    data = json.loads(repo.run("check", "--offline", "--format", "json").stdout)
    assert data["schema_version"] == 1


def test_frozen_rule_ids_are_still_present():
    # the rule ids of the 0.5.2 release; a rule may be added, never renamed or removed without a deprecation cycle
    frozen = """secrets.hardcoded secrets.generic secrets.env_file secrets.env_not_ignored deps.hallucinated deps.undeclared
    deps.new_package deps.version_cooldown deps.untrusted_source deps.local_source deps.typosquat deps.vulnerable deps.outdated
    deps.deprecated deps.removed_stdlib deps.unverified scope.forbidden_path scope.out_of_scope gate.config_changed
    tests.assertions_removed tests.skip_added tests.deleted tests.focused tests.trivial_assert tests.no_assertions
    tests.expected_changed tests.harness_tamper tests.config_changed tests.internals_touched suppress.security suppress.blanket
    suppress.type suppress.coverage suppress.lint suppress.type_cast code.syntax_error code.placeholder code.stub code.todo
    code.swallowed_exception code.bare_except code.debug_leftover code.console_log code.generate_directive code.mutable_default
    code.unused_import code.duplicate_block security.eval security.shell security.deserialization security.sql_injection
    security.verification_disabled security.insecure_default security.weak_crypto security.xss size.function_length
    size.complexity size.file_length size.diff_budget perf.nested_loops perf.quadratic_pattern perf.call_in_loop
    perf.exponential_recursion api.unknown_symbol api.bad_call api.broken_caller tests.missing_for_change review.claim_mismatch
    code.error_check_removed commands.failed commands.untrusted gate.config_invalid gate.version_too_old gate.version_too_new
    gate.error""".split()
    missing = [r for r in frozen if r not in aigate.RULES]
    assert not missing, f"frozen rule ids missing: {missing}"
