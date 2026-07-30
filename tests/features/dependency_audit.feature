Feature: Read-only dependency gate audit

  Scenario: Declared and unexpected opaque board links are all retained
    Given a normalized dependency board with matching missing and extra links
    When dependency edges are audited
    Then every declared edge is attached or missing and extras are unexpected

  Scenario: Child starts are classified strictly against parent merge gates
    Given merged and unmerged parent PR facts with boundary child starts
    When dependency edges are audited
    Then child starts are before after equal unmerged or not observed exactly

  Scenario: Failing prerequisite waits retain strict retry intervention evidence
    Given canonical and unclassified prerequisite waits with retries and comments
    When dependency edges are audited
    Then waits and intervention results use exact strict timestamps

  Scenario: GitHub facts use deterministic query-only bounded batches
    Given canonical pull request references across hosts and repositories
    When GitHub facts are fetched through the fake runner
    Then GitHub receives version then query-only batches of at most fifty aliases

  Scenario: Unavailable or contradictory dependency evidence fails closed
    Given dependency acquisition and handoff failures
    When invalid dependency evidence is audited or fetched
    Then a specific boundary error is returned without a partial audit
