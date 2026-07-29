Feature: Canonical lifecycle normalization and metrics

  Scenario: Completed chunks with multiple in-period canonical verdicts
    Given completed chunks with multiple in-period canonical verdicts
    When lifecycle metrics are calculated
    Then each bounced chunk enters the numerator once and all verdict ids remain evidence
    And each score dimension has its own exact Decimal mean

  Scenario: No canonical verdicts or a completed chunk without one
    Given no canonical verdicts and a completed chunk without one
    When absent-verdict lifecycle metrics are calculated
    Then quality and a zero-denominator bounce value are unavailable
    And missing canonical verdict coverage is listed

  Scenario: Paired and conflicting block evidence
    Given paired block evidence, exact reasons, native kinds, an unknown schema, and a conflict
    When block evidence is normalized and measured
    Then paired block evidence is deduplicated and exact reasons are counted
    And noncanonical block evidence is warned and unclassified
    And conflicting canonical block reasons fail closed

  Scenario: Exact actor classes and operator intervention
    Given exact operator, worker, prejudge, automated, and unknown authors around lifecycle events
    When intervention metrics are calculated
    Then only strict post-dispatch pre-terminal operator comments count
    And a block-comment-next-claim chain counts its chunk once

  Scenario: Period bounds and equal causal timestamps
    Given records at both period bounds and equal causal timestamps
    When boundary lifecycle metrics are calculated
    Then the lower bound contributes and the upper bound does not
    And minimal boundary history is context only
    And timestamp ties are indeterminate rather than ordered
