import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import RawDataTable from './RawDataTable';

describe('RawDataTable', () => {
  it('shows an empty state when a query returns no rows', () => {
    render(<RawDataTable rows={[]} />);

    expect(screen.getByText('Raw data')).toBeInTheDocument();
    expect(screen.getByText('No rows returned.')).toBeInTheDocument();
  });

  it('renders all row columns in a bounded scrolling table with a sticky header', () => {
    const { container } = render(
      <RawDataTable rows={[
        { id: 1, name: 'Alpha' },
        { id: 2, name: 'Beta', active: true },
      ]} />,
    );

    expect(screen.getByRole('columnheader', { name: 'id' })).toHaveClass('sticky');
    expect(screen.getByRole('columnheader', { name: 'active' })).toBeInTheDocument();
    expect(screen.getByText('Alpha')).toBeInTheDocument();
    expect(screen.getByText('true')).toBeInTheDocument();
    expect(container.querySelector('.max-h-72.overflow-auto')).toBeInTheDocument();
  });
});
