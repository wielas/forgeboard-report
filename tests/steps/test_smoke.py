"""Smoke steps — proves the pytest-bdd plumbing before any real chunk."""

import re

from pytest_bdd import given, scenarios, then, when

import forgeboard_report

scenarios("../features/smoke.feature")


@given("the package is installed", target_fixture="pkg")
def pkg():
    return forgeboard_report


@when("I read its version", target_fixture="version")
def version(pkg):
    return pkg.__version__


@then("it is a semver string")
def is_semver(version):
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)
