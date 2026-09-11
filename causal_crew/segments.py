"""SQL filter for a segment, shared by the investigator and the judge."""

from causal_crew import config as C


def segment_filter(segment):
    """Return (where_clause, params). Dimension names are checked against
    config; values are always bound as parameters, never interpolated."""
    for dim in segment:
        if dim not in C.DIMENSIONS:
            raise ValueError(f"unknown dimension: {dim!r}")
    if not segment:
        return "TRUE", []
    return " AND ".join(f"{dim} = ?" for dim in segment), list(segment.values())
