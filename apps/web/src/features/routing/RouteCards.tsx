'use client';

import { type KeyboardEvent, useId } from 'react';
import type { Route, RouteCompareResponse } from '@pathable/contracts';
import { Icon } from '@/components/Icon';
import {
  type RouteDuration,
  type RouteExclusion,
  type RouteVariant,
  exclusionLabel,
  exclusionsOf,
  formatDurationRange,
  formatDurationShort,
  formatShare,
  gradeBasis,
  paceLabel,
  paceNote,
  routeDuration,
  verdictOf,
} from './route-evidence';
import { formatDistance } from './types';
import styles from './Planner.module.css';

/**
 * The two routes as the Golden Master's comparison cards (9:2117, 9:2149).
 *
 * The cards are a radio group because choosing one is choosing which line the
 * map brings forward; the arrow keys move between them. The other route always
 * stays drawn: a viewer who picks one has been shown it more clearly, not
 * shown that it is safe.
 *
 * Every figure is the response's. Each card's time is its own route's, at the
 * pace that route was timed at — walking pace for the shortest route, the
 * profile's assumed pace for the other — and the two are never subtracted:
 * part of the gap would be a difference in assumed pace, not in route.
 */
export function RouteCards({
  comparison,
  profileName,
  selectedRoute,
  onSelectRoute,
}: {
  readonly comparison: RouteCompareResponse;
  /** The profile's name as the traveller chose it (see `journeyProfileName`). */
  readonly profileName: string;
  readonly selectedRoute: RouteVariant;
  readonly onSelectRoute: (variant: RouteVariant) => void;
}) {
  const { standard_route: standard, accessible_route: accessible } = comparison;
  if (!standard || !accessible) return null;

  // The arrow keys move the choice and the focus together, as a radio group
  // does. With two options every arrow goes to the other one.
  const move = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
    event.preventDefault();
    const next: RouteVariant = selectedRoute === 'accessible' ? 'standard' : 'accessible';
    onSelectRoute(next);
    event.currentTarget
      .closest('[role="radiogroup"]')
      ?.querySelector<HTMLButtonElement>(`[role="radio"][data-variant="${next}"]`)
      ?.focus();
  };

  return (
    <section className={styles.cards} aria-label="The two routes" data-testid="route-difference">
      <div className={styles.cards} role="radiogroup" aria-label="Route drawn in front on the map">
        <ProfileCard
          comparison={comparison}
          profileName={profileName}
          route={accessible}
          other={standard}
          checked={selectedRoute === 'accessible'}
          onSelect={onSelectRoute}
          onKeyDown={move}
        />
        <ShortestCard
          comparison={comparison}
          profileName={profileName}
          route={standard}
          other={accessible}
          checked={selectedRoute === 'standard'}
          onSelect={onSelectRoute}
          onKeyDown={move}
        />
      </div>
    </section>
  );
}

type CardProps = {
  readonly comparison: RouteCompareResponse;
  readonly profileName: string;
  readonly route: Route;
  readonly other: Route;
  readonly checked: boolean;
  readonly onSelect: (variant: RouteVariant) => void;
  readonly onKeyDown: (event: KeyboardEvent<HTMLButtonElement>) => void;
};

function difference(comparison: RouteCompareResponse): string {
  const verdict = verdictOf(comparison);
  switch (verdict.kind) {
    case 'detour':
      return `+${formatDistance(verdict.extraM)}`;
    case 'shorter':
      return `${formatDistance(verdict.savedM)} shorter`;
    case 'same_length':
    case 'same_route':
      return 'Same length';
    default:
      return 'Difference not reported';
  }
}

/** A route's time and whose pace it assumes, both from the response. */
function Duration({
  duration,
  testId,
}: {
  readonly duration: RouteDuration;
  readonly testId: string;
}) {
  return (
    <span
      className={styles.time}
      data-kind="estimate"
      data-testid={testId}
      // The card has room for one line; the full sentence is the accessible
      // description and the tooltip.
      title={`${formatDurationRange(duration)} ${paceNote(duration)}`}
    >
      {formatDurationShort(duration)}
      <span className={styles.paceNote}> · {paceLabel(duration)}</span>
    </span>
  );
}

/** "Your wheelchair route": the traveller's own, named as they chose it. */
export function profileRouteName(profileName: string): string {
  return `Your ${profileName.toLowerCase()} route`;
}

function ProfileCard({ comparison, profileName, route, checked, onSelect, onKeyDown }: CardProps) {
  const titleId = useId();
  const valueId = useId();
  const climb = route.gradient.steepest_uphill;
  const surfaceGap = route.evidence_coverage?.['surface'];

  return (
    <button
      type="button"
      role="radio"
      aria-checked={checked}
      tabIndex={checked ? 0 : -1}
      aria-labelledby={`${titleId} ${valueId}`}
      className={styles.card}
      data-variant="accessible"
      data-testid="difference-accessible"
      onClick={() => onSelect('accessible')}
      onKeyDown={onKeyDown}
    >
      <span className={styles.cardHead}>
        <span className={styles.cardName} id={titleId}>
          <span className={styles.swatch} data-variant="accessible" aria-hidden="true" />
          {profileRouteName(profileName)}
        </span>
      </span>
      <span className={styles.figures}>
        <span className={styles.figureMain} id={valueId}>
          <span className={styles.distance}>{formatDistance(route.distance_m)}</span>
          <Duration duration={routeDuration(route)} testId="difference-accessible-time" />
        </span>
        <span className={styles.delta} data-testid="difference-extra">
          {difference(comparison)}
        </span>
      </span>
      <span className={styles.tiles}>
        <Tile
          label="Stairs"
          value={`${route.stairway_count} recorded`}
          tone={route.stairway_count === 0 ? 'good' : 'neutral'}
        />
        <Tile
          label={
            climb === null ? 'Grade' : gradeBasis(climb) === 'recorded' ? 'Grade' : 'Est. grade'
          }
          value={climb === null ? 'Not recorded' : `Max ${climb.percent.toFixed(1)}%`}
          tone={climb === null ? 'unknown' : 'neutral'}
          testId="steepest-climb-tile"
        />
        <Tile
          label="Surface"
          value={
            typeof surfaceGap === 'number'
              ? `${formatShare(1 - Math.min(1, Math.max(0, surfaceGap)))} Recorded`
              : 'Not reported'
          }
          tone={typeof surfaceGap === 'number' ? 'neutral' : 'unknown'}
        />
      </span>
    </button>
  );
}

function Tile({
  label,
  value,
  tone,
  testId,
}: {
  readonly label: string;
  readonly value: string;
  readonly tone: 'good' | 'neutral' | 'unknown';
  readonly testId?: string;
}) {
  return (
    <span className={styles.tileBox}>
      <span className={styles.tileLabel}>{label}</span>
      <span
        className={styles.tileValue}
        data-tone={tone}
        {...(testId ? { 'data-testid': testId } : {})}
      >
        {value}
      </span>
    </span>
  );
}

/** What a ruled-out group reads as on the shortest card. */
function exclusionChip(exclusion: RouteExclusion): string {
  return exclusion.reason === 'steps'
    ? `${exclusion.count} recorded ${exclusion.count === 1 ? 'stairway' : 'stairways'}`
    : exclusionLabel(exclusion);
}

function ShortestCard({ profileName, route, checked, onSelect, onKeyDown }: CardProps) {
  const titleId = useId();
  const valueId = useId();
  const exclusions = exclusionsOf(route);
  const climb = route.gradient.steepest_uphill;
  const steepRuledOut = exclusions.some((exclusion) => exclusion.reason === 'too_steep');
  const stairsRuledOut = exclusions.some((exclusion) => exclusion.reason === 'steps');

  return (
    <button
      type="button"
      role="radio"
      aria-checked={checked}
      tabIndex={checked ? 0 : -1}
      aria-labelledby={`${titleId} ${valueId}`}
      className={styles.card}
      data-variant="standard"
      data-testid="difference-shortest"
      onClick={() => onSelect('standard')}
      onKeyDown={onKeyDown}
    >
      <span className={styles.cardHead}>
        <span className={styles.cardName} id={titleId}>
          <span className={styles.swatch} data-variant="standard" aria-hidden="true" />
          Shortest pedestrian route
        </span>
        {exclusions.length > 0 ? (
          <span className={styles.chip} data-tone="barrier">
            Hard barriers
          </span>
        ) : null}
      </span>
      <span className={styles.figures}>
        <span className={styles.figureMain} id={valueId}>
          <span className={styles.distance}>{formatDistance(route.distance_m)}</span>
          <Duration duration={routeDuration(route)} testId="difference-shortest-time" />
        </span>
      </span>
      {exclusions.length > 0 || (climb !== null && !steepRuledOut) || route.stairway_count > 0 ? (
        <span className={styles.chips} data-testid="route-blocked">
          {exclusions.map((exclusion) => (
            <span
              key={exclusion.reason}
              className={`${styles.chip} ${styles.chipMono}`}
              data-tone="barrier"
            >
              <Icon name={exclusion.reason === 'too_steep' ? 'trending-up' : 'stairs'} size={16} />
              {exclusionChip(exclusion)}
            </span>
          ))}
          {!stairsRuledOut && route.stairway_count > 0 ? (
            <span className={`${styles.chip} ${styles.chipMono}`} data-tone="neutral">
              <Icon name="stairs" size={16} />
              {route.stairway_count} recorded{' '}
              {route.stairway_count === 1 ? 'stairway' : 'stairways'}
            </span>
          ) : null}
          {climb !== null && !steepRuledOut ? (
            <span className={`${styles.chip} ${styles.chipMono}`} data-tone="unknown">
              <Icon name="trending-up" size={16} />
              {climb.percent.toFixed(1)}% steepest climb
            </span>
          ) : null}
        </span>
      ) : null}
      <span className="visually-hidden">
        {exclusions.length > 0 ? `Ruled out for the ${profileName.toLowerCase()} profile.` : ''}
      </span>
    </button>
  );
}
