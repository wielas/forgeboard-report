Feature: Validated request and graph contracts
  Misleading inputs fail before any board or pull-request source is acquired.

  Scenario: Valid inputs retain normalized request and graph evidence
    Given a valid board, exact operators, aware bounds, and an acyclic graph
    When the request and graph inputs are resolved
    Then the original inputs and normalized graph evidence are retained
    And interval membership includes the lower bound and excludes the upper bound

  Scenario: Invalid operator, interval, and board inputs identify their fields
    Given naïve, unordered, invalid-operator, and traversal-shaped request inputs
    When each invalid request is resolved
    Then every request fails with a usage error naming its invalid field

  Scenario: Invalid graph relationships fail closed
    Given graphs with duplicate chunks, duplicate dependencies, missing endpoints, self-edges, and cycles
    When each invalid graph is loaded
    Then every graph fails with a specific invalid-core error

  Scenario: Equivalent graph records normalize deterministically
    Given equivalent valid graph records in different input orders
    When both graph byte sequences are loaded
    Then normalized chunks and edges have identical iteration order
    And each graph fingerprint identifies its exact source bytes
