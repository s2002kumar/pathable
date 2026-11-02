'use client';

import type { RouteCompareResponse } from '@pathable/contracts';
import { Icon } from '@/components/Icon';
import { summaryBasisLabel, summaryEvidenceSegments } from './route-evidence';
import styles from './Planner.module.css';

/**
 * The comparison in one line, read before anything else.
 *
 * "Same path as shortest route." or "+67 m to avoid 3 recorded stairways." —
 * the response's own sentence, never rebuilt here, with what its reason rests
 * on beneath it. Avoiding missing data is labelled as exactly that, so it never
 * reads as avoiding a recorded barrier.
 *
 * "Show on map" draws the shortest route's segments the reason is about. It is
 * outside the route cards on purpose: those are radio buttons, and a button
 * inside a button is neither valid nor operable.
 */
export function ComparisonSummary({
  comparison,
  highlighted = false,
  onToggleHighlight,
}: {
  readonly comparison: RouteCompareResponse;
  readonly highlighted?: boolean;
  readonly onToggleHighlight?: () => void;
}) {
  const summary = comparison.summary;
  if (!summary?.headline) return null;
  const basis = summaryBasisLabel(comparison);
  const canHighlight =
    onToggleHighlight !== undefined && summaryEvidenceSegments(comparison).length > 0;

  return (
    <div className={styles.summary} data-kind={summary.kind} data-testid="comparison-summary">
      <p className={styles.summaryHeadline} data-testid="comparison-headline">
        {summary.headline}
      </p>
      {basis ? (
        <p
          className={styles.summaryBasis}
          data-basis={summary.primary_basis ?? undefined}
          data-testid="comparison-basis"
        >
          {basis}
        </p>
      ) : null}
      {canHighlight ? (
        <button
          type="button"
          className={styles.summaryButton}
          aria-pressed={highlighted}
          onClick={onToggleHighlight}
          data-testid="show-evidence-on-map"
        >
          <Icon name="eye" size={16} />
          {highlighted ? 'Hide on map' : 'Show on map'}
        </button>
      ) : null}
    </div>
  );
}
