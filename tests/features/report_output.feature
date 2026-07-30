Feature: Canonical report output
  The report bundle is rendered from one immutable model.

  Scenario: Render matching machine and paste-ready projections
    Given a complete canonical report model
    When its projections are rendered
    Then the JSON schema order and Markdown section order are stable
    And unavailable values remain explicit in the projections
    And Markdown carries the matching projected JSON values and stable IDs

  Scenario: Repeated rendering preserves report bytes and identifiers
    Given a complete canonical report model
    When its projections are rendered twice
    Then both renderings are byte-identical

  Scenario: Rendering is deterministic across source order and process settings
    Given equivalent reports with shuffled source insertion under varied process settings
    When their projections are rendered
    Then their JSON and Markdown bytes are identical UTF-8 LF output

  Scenario: Rendering and publication preserve the source-byte fingerprint
    Given a report with immutable source bytes and their fingerprint
    When its projections are rendered and published
    Then the source bytes and their fingerprint are preserved in the output projections

  Scenario: Injected renderer failure leaves no report destination
    Given a complete canonical report model
    And an injected renderer failure
    When the full report is rendered and published
    Then the renderer failure is invalid core and no destination appears

  Scenario Outline: Publication failure leaves no plausible report
    Given a complete canonical report model
    And an injected <stage> publication failure
    When the projections are published
    Then the <stage> failure is typed and only private staging was cleaned up

    Examples:
      | stage  |
      | write  |
      | flush  |
      | rename |
