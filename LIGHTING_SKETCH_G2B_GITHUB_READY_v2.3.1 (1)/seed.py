"""Sample-data support is permanently disabled.

This application now operates with public/procurement source data only.
Legacy generated rows can still be removed safely by clear_samples().
"""
from db import connect


def seed_if_empty():
    """Disabled permanently: never create generated/sample records."""
    return 0


def clear_samples():
    """Delete only legacy generated rows; real rows (is_sample=0) are untouched."""
    with connect() as conn:
        a = conn.execute('DELETE FROM shopping_contracts WHERE is_sample=1').rowcount
        b = conn.execute('DELETE FROM bids WHERE is_sample=1').rowcount
        c = conn.execute('DELETE FROM budget_items WHERE is_sample=1').rowcount
    return a + b + c


if __name__ == '__main__':
    print('removed_sample_rows', clear_samples())
