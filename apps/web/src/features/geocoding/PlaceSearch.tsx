'use client';

import { type ReactNode, useCallback, useEffect, useId, useRef, useState } from 'react';
import { API_ROUTES, type GeocodeMatch, type GeocodeResponse } from '@pathable/contracts';
import { Icon } from '@/components/Icon';
import styles from './PlaceSearch.module.css';

/** Bounded so a slow geocoder cannot leave the form spinning. */
const SEARCH_TIMEOUT_MS = 15_000;
/** A pause in typing this long asks for suggestions. */
export const SUGGEST_DELAY_MS = 180;
/** Fewer characters than this ask for nothing: one letter matches half the city. */
export const SUGGEST_MIN_CHARACTERS = 2;

type SearchState =
  | { readonly status: 'idle' }
  | { readonly status: 'searching' }
  | { readonly status: 'disabled' }
  | {
      readonly status: 'results';
      /** The text these results answer, so a late answer cannot replace a newer one. */
      readonly query: string;
      readonly matches: readonly GeocodeMatch[];
      readonly attribution: string | null;
    }
  | { readonly status: 'error'; readonly message: string };

export type PlaceSearchProps = {
  readonly apiBaseUrl: string;
  readonly region: string;
  readonly onSelect: (position: { longitude: number; latitude: number }, label: string) => void;
  readonly fetchImpl?: typeof fetch;
  /**
   * Per-instance identity. The defaults are the single-field wording this
   * component shipped with, so one on its own is unchanged; a page with two
   * must give each a distinct accessible name, or every query for "Search for
   * a place" becomes ambiguous and the announcements land on the wrong field.
   */
  readonly label?: string;
  readonly placeholder?: string;
  readonly submitAccessibleName?: string;
  readonly testId?: string;
  /** The input's id, so another control (the header's search) can focus it. */
  readonly inputId?: string;
  /**
   * What the field currently resolves to. Shown as the input's text until the
   * viewer types something else, so the field reads "UW Davis Centre" rather
   * than an empty box beside a separate line naming it.
   */
  readonly currentValue?: string | null;
  /** Points at text describing the current value, for assistive technology. */
  readonly describedBy?: string;
  /** Controls drawn inside the field, after the input: swap, set on map, clear. */
  readonly trailing?: ReactNode;
  /** Drawn before the label: the endpoint's coloured dot. */
  readonly leading?: ReactNode;
  readonly className?: string;
  /** Rendered after the field: anything the field wants to say about itself. */
  readonly children?: ReactNode;
};

/** How a suggestion matched, in a word, when it was not by its name. */
function matchedOnNote(match: GeocodeMatch): string | null {
  switch (match.matched_on) {
    case 'code':
      // OpenStreetMap's ref or short_name: a campus building's code, but also
      // a restaurant's short name. "building code" told people a restaurant
      // was a building called DC.
      return 'short code';
    case 'initialism':
      return 'initials';
    case 'fuzzy':
      return 'closest spelling';
    default:
      return null;
  }
}

function optionName(match: GeocodeMatch): string {
  return match.category ? `${match.label}, ${match.category}` : match.label;
}

/**
 * Place-name search, drawn as the Golden Master's field: a small caps label
 * over the value, inside one surface.
 *
 * Suggests as somebody types, as a combobox: a pause in typing asks the place
 * index, the arrow keys move through what comes back, Enter takes the
 * highlighted place or, with none highlighted, searches what was typed. The
 * index is PathAble's own, so a request per pause is a query against its own
 * database. A provider that may not be searched as somebody types — a donated
 * one, under its usage policy — answers "not searched", and the field falls
 * back to searching only when asked.
 *
 * When there is nothing to search — no provider, or no index built for the
 * region — the API says so, and this says so too rather than reporting "no
 * results", which would send someone off to rephrase a query that was never
 * looked at.
 */
export function PlaceSearch({
  apiBaseUrl,
  region,
  onSelect,
  fetchImpl,
  label = 'Search for a place',
  placeholder = 'e.g. Waterloo Public Square',
  submitAccessibleName,
  testId = 'place-search',
  inputId: providedInputId,
  currentValue = null,
  describedBy,
  trailing,
  leading,
  className,
  children,
}: PlaceSearchProps) {
  // The text the viewer has typed, or null while the field shows its value.
  const [draft, setDraft] = useState<string | null>(null);
  const [state, setState] = useState<SearchState>({ status: 'idle' });
  const [active, setActive] = useState(-1);
  const [open, setOpen] = useState(false);
  // Set when the provider answers an as-you-type search with "not searched":
  // from then on this field searches only when asked.
  const [suggestOff, setSuggestOff] = useState(false);
  const inFlight = useRef<AbortController | null>(null);
  const pause = useRef<ReturnType<typeof setTimeout> | null>(null);
  const answered = useRef(new Map<string, GeocodeResponse>());
  const generatedId = useId();
  const inputId = providedInputId ?? generatedId;
  const listboxId = `${inputId}-suggestions`;

  const query = draft ?? currentValue ?? '';
  const searchable = draft !== null && draft.trim().length > 0 && draft !== currentValue;
  const matches = state.status === 'results' ? state.matches : [];
  const expanded = open && matches.length > 0;

  useEffect(
    () => () => {
      if (pause.current !== null) clearTimeout(pause.current);
      inFlight.current?.abort();
    },
    [],
  );

  const search = useCallback(
    async (text: string, suggest: boolean) => {
      const key = `${suggest ? 'suggest' : 'search'}:${text}`;
      const known = answered.current.get(key);

      inFlight.current?.abort();
      const controller = new AbortController();
      inFlight.current = controller;
      const timer = setTimeout(() => controller.abort(), SEARCH_TIMEOUT_MS);
      if (!suggest) setState({ status: 'searching' });

      try {
        let payload: GeocodeResponse | null = known ?? null;
        if (payload === null) {
          const doFetch = fetchImpl ?? globalThis.fetch;
          const response = await doFetch(`${apiBaseUrl}${API_ROUTES.searchPlaces}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
            body: JSON.stringify({
              region,
              query: text,
              ...(suggest ? { autocomplete: true } : {}),
            }),
            signal: controller.signal,
            cache: 'no-store',
          });
          const body: unknown = await response.json().catch(() => null);

          if (!response.ok) {
            // A suggestion that fails says nothing: the person is still typing,
            // and a search they ask for will say what went wrong.
            if (suggest) return;
            const message =
              typeof body === 'object' && body !== null && 'message' in body
                ? String((body as { message: unknown }).message)
                : `Search failed (HTTP ${response.status}).`;
            setState({ status: 'error', message });
            return;
          }
          payload = body as GeocodeResponse | null;
          if (payload !== null) answered.current.set(key, payload);
        }

        if (controller.signal.aborted) return;
        if (payload === null) {
          if (!suggest) {
            setState({ status: 'error', message: 'The search service returned nothing readable.' });
          }
        } else if (!payload.enabled) {
          if (suggest) {
            setSuggestOff(true);
          } else {
            setState({ status: 'disabled' });
          }
        } else {
          setState({
            status: 'results',
            query: text,
            matches: payload.matches,
            attribution: payload.attribution ?? null,
          });
          setActive(-1);
          setOpen(true);
        }
      } catch (error) {
        // A suggestion that fails, or a request a newer one replaced, says
        // nothing: only a search somebody asked for reports a failure.
        if (suggest || inFlight.current !== controller) return;
        const timedOut = error instanceof Error && error.name === 'AbortError';
        setState({
          status: 'error',
          message: timedOut
            ? 'The search took too long. Try again, or click the map instead.'
            : 'The search service could not be reached. Click the map instead.',
        });
      } finally {
        clearTimeout(timer);
      }
    },
    [apiBaseUrl, region, fetchImpl],
  );

  const choose = useCallback(
    (match: GeocodeMatch) => {
      onSelect({ longitude: match.longitude, latitude: match.latitude }, match.label);
      setState({ status: 'idle' });
      setDraft(null);
      setOpen(false);
      setActive(-1);
    },
    [onSelect],
  );

  const handleChange = (text: string) => {
    setDraft(text);
    if (pause.current !== null) clearTimeout(pause.current);
    const trimmed = text.trim();
    if (suggestOff || trimmed.length < SUGGEST_MIN_CHARACTERS || text === currentValue) {
      if (trimmed.length === 0) {
        setState({ status: 'idle' });
        setOpen(false);
      }
      return;
    }
    pause.current = setTimeout(() => {
      pause.current = null;
      void search(trimmed, true);
    }, SUGGEST_DELAY_MS);
  };

  const submit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (expanded && active >= 0 && matches[active]) {
      choose(matches[active]);
      return;
    }
    if (draft === null) return;
    const text = draft.trim();
    if (text.length === 0 || draft === currentValue) return;
    if (pause.current !== null) clearTimeout(pause.current);
    void search(text, false);
  };

  const status = state.status;
  const activeId = expanded && active >= 0 ? `${listboxId}-${active}` : undefined;

  return (
    <form
      className={[styles.form, className].filter(Boolean).join(' ')}
      onSubmit={submit}
      data-testid={testId}
    >
      <div className={styles.field}>
        {leading}
        <div className={styles.body}>
          <label className={styles.label} htmlFor={inputId}>
            {label}
          </label>
          <input
            id={inputId}
            className={styles.input}
            type="search"
            role="combobox"
            aria-autocomplete="list"
            aria-expanded={expanded}
            aria-controls={listboxId}
            {...(activeId ? { 'aria-activedescendant': activeId } : {})}
            value={query}
            onChange={(event) => handleChange(event.target.value)}
            onFocus={(event) => {
              // Replacing a place is the common edit: select it so typing
              // starts over rather than appending to "UW Davis Centre".
              if (draft === null && currentValue) event.currentTarget.select();
              if (matches.length > 0) setOpen(true);
            }}
            onBlur={() => setOpen(false)}
            onKeyDown={(event) => {
              if (event.key === 'ArrowDown' && matches.length > 0) {
                event.preventDefault();
                setOpen(true);
                setActive((current) => Math.min(current + 1, matches.length - 1));
              } else if (event.key === 'ArrowUp' && expanded) {
                event.preventDefault();
                setActive((current) => Math.max(current - 1, 0));
              } else if (event.key === 'Escape') {
                if (expanded) {
                  // First Escape closes the suggestions; the text stays.
                  event.preventDefault();
                  setOpen(false);
                  setActive(-1);
                } else if (draft !== null) {
                  setDraft(null);
                  setState({ status: 'idle' });
                }
              }
            }}
            placeholder={placeholder}
            autoComplete="off"
            // No aria-live on the input: results are announced by the region below.
            enterKeyHint="search"
            {...(describedBy ? { 'aria-describedby': describedBy } : {})}
          />
        </div>
        {searchable ? (
          <button
            type="submit"
            className={styles.button}
            aria-label={submitAccessibleName ?? 'Search'}
            title="Search"
          >
            <Icon name="search" size={16} />
          </button>
        ) : null}
        {trailing}
      </div>

      {children}

      {/* Always present, so aria-controls always points somewhere; empty and
          hidden while there is nothing to offer. */}
      <ul
        className={styles.matchList}
        role="listbox"
        id={listboxId}
        // Not named after the field: a name containing "Start location" made
        // every query for the field's label find two elements.
        aria-label="Suggested places"
        hidden={!expanded}
        data-testid={`${testId}-suggestions`}
      >
        {matches.map((match, index) => {
          const note = matchedOnNote(match);
          return (
            <li
              key={`${match.label}:${match.longitude}:${match.latitude}`}
              id={`${listboxId}-${index}`}
              role="option"
              aria-selected={index === active}
              // Said as one phrase. Left to the lines below, some browsers run
              // the name, the category and the note together.
              aria-label={optionName(match)}
              className={styles.match}
              data-active={index === active}
              // Keeps focus in the field, so choosing does not blur it first
              // and close the list under the pointer.
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => choose(match)}
            >
              <span>{match.label}</span>
              {/* What the map calls it — a library, a street, an address —
                  because two results can share a name; and how it matched
                  when that was not by its name. */}
              {match.category || note ? (
                <span className={styles.matchCategory}>
                  {[match.category, note].filter(Boolean).join(' · ')}
                </span>
              ) : null}
            </li>
          );
        })}
      </ul>

      <div className={styles.results} aria-live="polite" data-search-state={status}>
        {status === 'searching' ? <p className={styles.note}>Searching…</p> : null}

        {status === 'disabled' ? (
          <p className={styles.note}>
            Place search is not available here. Click the map to choose points.
          </p>
        ) : null}

        {status === 'error' ? (
          <p className={styles.error} role="alert">
            {state.message}
          </p>
        ) : null}

        {status === 'results' && state.matches.length === 0 ? (
          <p className={styles.note}>
            Nothing matched that inside this area. Try a different name, or click the map.
          </p>
        ) : null}

        {expanded ? (
          <p className="visually-hidden">
            {matches.length} {matches.length === 1 ? 'place' : 'places'} found. Use the arrow keys
            to choose.
          </p>
        ) : null}

        {/* The API returns the provider's attribution with every result set
            specifically so a client cannot display results without it. */}
        {expanded && state.status === 'results' && state.attribution ? (
          <p className={styles.attribution} data-testid="search-attribution">
            {state.attribution}
          </p>
        ) : null}
      </div>
    </form>
  );
}
