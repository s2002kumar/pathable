import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { PlaceSearch, SUGGEST_DELAY_MS } from './PlaceSearch';

const MATCHES = [
  {
    label: 'Waterloo Public Square, King Street South, Waterloo, Ontario',
    longitude: -80.523,
    latitude: 43.4658,
    category: 'square',
  },
  {
    label: 'Waterloo Park, Waterloo, Ontario',
    longitude: -80.533,
    latitude: 43.469,
    category: 'park',
  },
];

function jsonFetch(body: unknown, status = 200) {
  return vi.fn(
    async () =>
      new Response(JSON.stringify(body), {
        status,
        headers: { 'Content-Type': 'application/json' },
      }),
  ) as unknown as typeof fetch;
}

function bodies(fetchImpl: typeof fetch): Array<Record<string, unknown>> {
  return (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock.calls.map(
    ([, init]) => JSON.parse(String((init as RequestInit).body)) as Record<string, unknown>,
  );
}

function renderSearch(fetchImpl: typeof fetch, onSelect = vi.fn()) {
  render(
    <PlaceSearch
      apiBaseUrl="http://api.test"
      region="waterloo"
      onSelect={onSelect}
      fetchImpl={fetchImpl}
    />,
  );
  return { onSelect };
}

/** Lets the pause in typing pass, so the suggestion request goes out. */
async function pause() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, SUGGEST_DELAY_MS + 50));
  });
}

describe('PlaceSearch', () => {
  it('suggests places once typing pauses, as one request, marked as a suggestion', async () => {
    const user = userEvent.setup();
    const fetchImpl = jsonFetch({ provider: 'local', enabled: true, matches: MATCHES });
    renderSearch(fetchImpl);

    await user.type(screen.getByRole('combobox'), 'Waterloo');
    await pause();

    await waitFor(() => expect(fetchImpl).toHaveBeenCalledOnce());
    expect(bodies(fetchImpl)[0]).toEqual({
      region: 'waterloo',
      query: 'Waterloo',
      autocomplete: true,
    });
    expect(await screen.findByRole('option', { name: /Waterloo Park/ })).toBeVisible();
    expect(screen.getByRole('combobox')).toHaveAttribute('aria-expanded', 'true');
  });

  it('asks for nothing until two characters are typed', async () => {
    const user = userEvent.setup();
    const fetchImpl = jsonFetch({ provider: 'local', enabled: true, matches: MATCHES });
    renderSearch(fetchImpl);

    await user.type(screen.getByRole('combobox'), 'W');
    await pause();

    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it('searches when the form is submitted, and keeps the query out of the URL', async () => {
    const user = userEvent.setup();
    const fetchImpl = jsonFetch({ provider: 'local', enabled: true, matches: MATCHES });
    renderSearch(fetchImpl);

    await user.type(screen.getByRole('combobox'), 'Waterloo Park');
    await user.click(screen.getByRole('button', { name: 'Search' }));

    await waitFor(() => expect(fetchImpl).toHaveBeenCalled());
    const [url, init] = (fetchImpl as unknown as ReturnType<typeof vi.fn>).mock.calls.at(-1) as [
      string,
      RequestInit,
    ];
    expect(url).toBe('http://api.test/api/v1/geocode/search');
    expect(init.method).toBe('POST');
    // The query is a statement about a person's plans; it does not go in a URL.
    expect(url).not.toContain('Waterloo');
  });

  it('chooses a suggestion with the arrow keys and Enter', async () => {
    const user = userEvent.setup();
    const fetchImpl = jsonFetch({ provider: 'local', enabled: true, matches: MATCHES });
    const { onSelect } = renderSearch(fetchImpl);

    await user.type(screen.getByRole('combobox'), 'Waterloo');
    await pause();
    await screen.findByRole('option', { name: /Waterloo Park/ });

    await user.keyboard('{ArrowDown}{ArrowDown}');
    const active = screen.getByRole('option', { name: /Waterloo Park/ });
    expect(active).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('combobox')).toHaveAttribute('aria-activedescendant', active.id);
    await user.keyboard('{Enter}');

    expect(onSelect).toHaveBeenCalledWith(
      { longitude: -80.533, latitude: 43.469 },
      'Waterloo Park, Waterloo, Ontario',
    );
  });

  it('chooses a suggestion with a click', async () => {
    const user = userEvent.setup();
    const fetchImpl = jsonFetch({ provider: 'local', enabled: true, matches: MATCHES });
    const { onSelect } = renderSearch(fetchImpl);

    await user.type(screen.getByRole('combobox'), 'Waterloo');
    await pause();
    await user.click(await screen.findByRole('option', { name: /Waterloo Public Square/ }));

    expect(onSelect).toHaveBeenCalledWith(
      { longitude: -80.523, latitude: 43.4658 },
      'Waterloo Public Square, King Street South, Waterloo, Ontario',
    );
  });

  it('shows what kind of place each match is, and how it matched', async () => {
    const user = userEvent.setup();
    const fetchImpl = jsonFetch({
      provider: 'local',
      enabled: true,
      matches: [
        {
          label: 'William G. Davis Computer Research Centre',
          longitude: -80.542,
          latitude: 43.4728,
          category: 'university building',
          kind: 'place',
          matched_on: 'code',
        },
        { label: 'Davis Street', longitude: -80.5, latitude: 43.45, category: null },
      ],
      attribution:
        'Places from OpenStreetMap as of 2026-08-16. © OpenStreetMap contributors, ODbL 1.0',
    });
    renderSearch(fetchImpl);

    await user.type(screen.getByRole('combobox'), 'DC');
    await pause();

    const building = await screen.findByRole('option', {
      name: 'William G. Davis Computer Research Centre, university building',
    });
    // A short code recorded in OpenStreetMap, said as one: not a guess at the
    // name, and not called a building code, because restaurants have them too.
    expect(building).toHaveTextContent('university building · short code');
    // A match with no category is named by its label alone, with no dangling comma.
    expect(screen.getByRole('option', { name: 'Davis Street' })).toBeVisible();
    expect(screen.getByTestId('search-attribution')).toHaveTextContent('as of 2026-08-16');
  });

  it('falls back to searching on request when the provider will not suggest', async () => {
    // A donated provider's policy forbids searching as somebody types; asked
    // to, the API reports "not searched", and the field stops asking.
    const user = userEvent.setup();
    const fetchImpl = jsonFetch({ provider: 'nominatim', enabled: false, matches: [] });
    renderSearch(fetchImpl);

    await user.type(screen.getByRole('combobox'), 'Waterloo');
    await pause();
    await waitFor(() => expect(fetchImpl).toHaveBeenCalledOnce());
    // Nothing is said about a suggestion that was not made.
    expect(screen.queryByText(/not available here/i)).not.toBeInTheDocument();

    await user.type(screen.getByRole('combobox'), ' Park');
    await pause();
    expect(fetchImpl).toHaveBeenCalledOnce();
  });

  it('distinguishes "search is off" from "nothing matched"', async () => {
    // Telling somebody their query found nothing, when it was never sent, sends
    // them off rephrasing a search that cannot work.
    const user = userEvent.setup();
    const fetchImpl = jsonFetch({ provider: 'disabled', enabled: false, matches: [] });
    renderSearch(fetchImpl);

    await user.type(screen.getByRole('combobox'), 'Waterloo');
    await user.click(screen.getByRole('button', { name: 'Search' }));

    expect(await screen.findByText(/place search is not available here/i)).toBeInTheDocument();
  });

  it('says so when nothing matched inside the region', async () => {
    const user = userEvent.setup();
    const fetchImpl = jsonFetch({ provider: 'local', enabled: true, matches: [] });
    renderSearch(fetchImpl);

    await user.type(screen.getByRole('combobox'), 'Atlantis');
    await user.click(screen.getByRole('button', { name: 'Search' }));

    expect(await screen.findByText(/nothing matched that inside this area/i)).toBeInTheDocument();
  });

  it('passes a provider failure through and points at the map instead', async () => {
    const user = userEvent.setup();
    const fetchImpl = jsonFetch(
      { code: 'geocoding_unavailable', message: 'The geocoding service is rate-limiting us.' },
      503,
    );
    renderSearch(fetchImpl);

    await user.type(screen.getByRole('combobox'), 'Waterloo');
    await user.click(screen.getByRole('button', { name: 'Search' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(/rate-limiting/);
  });

  it('recovers from a network failure without breaking the page', async () => {
    const user = userEvent.setup();
    const fetchImpl = vi.fn(async () => {
      throw new TypeError('Failed to fetch');
    }) as unknown as typeof fetch;
    renderSearch(fetchImpl);

    await user.type(screen.getByRole('combobox'), 'Waterloo');
    await user.click(screen.getByRole('button', { name: 'Search' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(/click the map instead/i);
  });

  it('will not submit an empty query', async () => {
    const user = userEvent.setup();
    const fetchImpl = jsonFetch({ provider: 'local', enabled: true, matches: [] });
    renderSearch(fetchImpl);

    // No submit control until there is something to search for, and Enter on
    // an empty field (or one holding only spaces) sends nothing.
    expect(screen.queryByRole('button', { name: 'Search' })).not.toBeInTheDocument();
    await user.type(screen.getByRole('combobox'), '   {Enter}');
    await pause();
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it('labels its input and its suggestions for assistive technology', () => {
    renderSearch(jsonFetch({ provider: 'disabled', enabled: false, matches: [] }));

    const input = screen.getByLabelText('Search for a place');
    expect(input).toHaveAttribute('role', 'combobox');
    expect(input).toHaveAttribute('aria-autocomplete', 'list');
    expect(document.getElementById(input.getAttribute('aria-controls') ?? '')).not.toBeNull();
  });

  it('announces results politely', () => {
    renderSearch(jsonFetch({ provider: 'disabled', enabled: false, matches: [] }));

    const region = screen.getByTestId('place-search').querySelector('[aria-live="polite"]');
    expect(region).not.toBeNull();
  });
});
