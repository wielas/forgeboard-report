Feature: Signed report command
  The public command produces one complete, traceable report bundle.

  Scenario: Successful report generation
    Given a valid recorded board, graph, exact operators, aware period, and fake merged/unmerged PR facts
    When the public command runs
    Then exactly two complete artifacts contain resolved fingerprints, all canonical metrics, dependency findings, warnings, and traceable stable ids

  Scenario: Invalid input exits 2
    Given invalid board slug, invalid ISO date, missing operator, or existing output directory
    When the command runs
    Then it exits 2 with a diagnostic on stderr and no report directory

  Scenario: Unavailable source exits 3
    Given an unknown board, missing graph file, or unavailable GitHub CLI
    When the command runs
    Then it exits 3 with a diagnostic on stderr and no report directory

  Scenario: Invalid core exits 4
    Given a cyclic graph or malformed canonical evidence
    When the command runs
    Then it exits 4 with a diagnostic on stderr and no report directory

  Scenario: Publication failure exits 5
    Given a simulated publication failure
    When the command runs
    Then it exits 5 with a diagnostic on stderr and no report directory
