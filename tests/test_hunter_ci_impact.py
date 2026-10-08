from hunter_ci_impact import select_tests


def test_changed_test_maps_to_itself():
    assert select_tests(["tests/test_foo.py"], {"tests/test_foo.py"})[1] == ("tests/test_foo.py",)


def test_script_maps_to_focused_test():
    assert select_tests(["scripts/hunter_pr_preflight.py"], {"tests/test_hunter_pr_preflight.py"})[1] == (
        "tests/test_hunter_pr_preflight.py",
    )


def test_unknown_script_fails_closed():
    assert select_tests(["scripts/unknown.py"], set())[0]


def test_workflow_requires_full():
    assert select_tests([".github/workflows/ci.yml"], {"tests/test_ci.py"})[0]


def test_empty_diff_requires_full():
    assert select_tests([], set())[0]
