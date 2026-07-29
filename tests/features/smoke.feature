Feature: Forge smoke
  The template's BDD plumbing works end to end.

  Scenario: package is importable and versioned
    Given the package is installed
    When I read its version
    Then it is a semver string
