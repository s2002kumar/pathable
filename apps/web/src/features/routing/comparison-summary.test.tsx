import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { highlightToGeoJson } from '@/features/map/route-layers';
import { comparison } from '@/test/route-fixtures';
import { ComparisonSummary } from './ComparisonSummary';

describe('the comparison in one line', () => {
  it('leads with the API’s own sentence and what its reason rests on', () => {
    render(<ComparisonSummary comparison={comparison()} />);

    expect(screen.getByTestId('comparison-headline')).toHaveTextContent(
      /^\+\d+ m to avoid 1 recorded stairway\.$/,
    );
    expect(screen.getByTestId('comparison-basis')).toHaveTextContent('Recorded in OpenStreetMap');
  });

  it('says “Same path as shortest route.” and offers nothing to point at', () => {
    render(
      <ComparisonSummary
        comparison={comparison({
          summary: {
            kind: 'same_path',
            headline: 'Same path as shortest route.',
            primary_code: null,
            primary_basis: null,
          },
        })}
        onToggleHighlight={vi.fn()}
      />,
    );

    expect(screen.getByTestId('comparison-headline')).toHaveTextContent(
      'Same path as shortest route.',
    );
    expect(screen.queryByTestId('comparison-basis')).not.toBeInTheDocument();
    expect(screen.queryByTestId('show-evidence-on-map')).not.toBeInTheDocument();
  });

  it('labels a detour around missing data as that, not as a barrier', () => {
    render(
      <ComparisonSummary
        comparison={comparison({
          summary: {
            kind: 'different_path',
            headline: '+90 m to avoid 2 crossings without a recorded kerb at both ends.',
            primary_code: 'avoids_unrecorded_kerbs',
            primary_basis: 'not_recorded',
          },
        })}
      />,
    );

    expect(screen.getByTestId('comparison-basis')).toHaveTextContent(
      'Avoids missing records, not a recorded barrier',
    );
  });

  it('shows and hides the evidence on the map, saying which it will do', async () => {
    const user = userEvent.setup();
    const toggle = vi.fn();
    const { rerender } = render(
      <ComparisonSummary comparison={comparison()} onToggleHighlight={toggle} />,
    );

    const button = screen.getByRole('button', { name: 'Show on map' });
    expect(button).toHaveAttribute('aria-pressed', 'false');
    await user.click(button);
    expect(toggle).toHaveBeenCalledOnce();

    rerender(
      <ComparisonSummary comparison={comparison()} highlighted onToggleHighlight={toggle} />,
    );
    expect(screen.getByRole('button', { name: 'Hide on map' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
  });
});

describe('what the map draws for the reason', () => {
  it('draws exactly the shortest-route segments the reason names', () => {
    const data = comparison();
    const drawn = highlightToGeoJson(data.standard_route, [1]);

    expect(drawn.features).toHaveLength(1);
    expect(drawn.features[0]?.properties).toMatchObject({ index: 1, edge_identity: 'way/11:0-1' });
    expect(drawn.features[0]?.geometry.coordinates).toEqual(
      data.standard_route!.segments[1]!.coordinates,
    );
  });

  it('skips a position the route does not have rather than guessing', () => {
    expect(highlightToGeoJson(comparison().standard_route, [99]).features).toEqual([]);
    expect(highlightToGeoJson(null, [1]).features).toEqual([]);
  });
});
