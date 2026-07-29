Feature: Stable read-only Hermes 0.19 snapshots

  Scenario: A stable board is captured without changing its live files
    Given a stable Hermes 0.19 database and sidecar set
    When the Hermes board is captured
    Then every required row and stable id is in the raw snapshot
    And representative rows agree with recorded public show JSON
    And the live Hermes source bytes are unchanged

  Scenario: Repeated source changes fail closed after three attempts
    Given a Hermes source that changes during every copy attempt
    When unstable Hermes capture is attempted
    Then capture fails as source-inconsistent after exactly three attempts
    And no partial raw Hermes snapshot is exposed

  Scenario: Archived bootstrap identities remain distinct from opaque task ids
    Given an archived card with the only matching bootstrap key and unrelated opaque task ids
    When the Hermes board is captured
    Then all task rows are retained without prose or task-id substitution

  Scenario: Invalid board sources fail specifically and do not write under Hermes
    Given unknown, missing, escaping, and incompatible Hermes board sources
    When each invalid Hermes source is captured
    Then each capture raises its specific actionable source or schema error
    And no invalid live Hermes root gains a file
