Feature: Canonical report output
  The report bundle is rendered from one immutable model.

  Scenario: Render matching machine and paste-ready projections
    Given a complete canonical report model
    When its projections are rendered
    Then the JSON schema order and Markdown section order are stable
    And unavailable values remain explicit in the projections
