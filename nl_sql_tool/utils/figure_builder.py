"""
Auto-detects appropriate chart type(s) from a list of row dicts and builds
Plotly figures. Returns a list of (title, figure) tuples — can be empty if
the data doesn't lend itself to a chart.
"""

import json
import re
import pandas as pd
import plotly.express as px
from plotly.graph_objects import Figure


def build_figures(rows: list[dict], user_query: str = "") -> list[tuple[str, Figure]]:
    """
    Analyses the DataFrame columns and returns up to 2 auto-detected charts.
    Returns [] if the data has no chartable structure.
    """
    if not rows:
        return []

    df = pd.DataFrame(rows)
    if df.empty:
        return []

    print(f"[figure_builder] Building figures for {len(df)} rows, {len(df.columns)} columns.")

    # Coerce numeric columns that arrived as strings
    df = _coerce_numeric(df)

    numeric_cols = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])]
    date_cols = _detect_date_columns(df)
    categorical_cols = [
        c for c in df.columns
        if c not in numeric_cols and c not in date_cols
        and df[c].nunique() <= 50  # skip high-cardinality text columns
    ]

    print(
        f"[figure_builder] numeric={numeric_cols}, date={date_cols}, "
        f"categorical={categorical_cols}"
    )

    figures: list[tuple[str, Figure]] = []

    # ── Time series: date col + numeric col ──────────────────────────────
    if date_cols and numeric_cols:
        d_col = date_cols[0]
        n_col = numeric_cols[0]
        try:
            df_sorted = df[[d_col, n_col]].dropna().sort_values(d_col)
            fig = px.line(df_sorted, x=d_col, y=n_col, markers=True)
            fig.update_layout(margin=dict(t=30, b=30))
            figures.append((f"{n_col} over {d_col}", fig))
            print(f"[figure_builder] Created line chart: {n_col} over {d_col}")
        except Exception as e:
            print(f"[figure_builder] Line chart failed: {e}")

    # ── Bar chart: categorical + numeric ────────────────────────────────
    if categorical_cols and numeric_cols:
        c_col = categorical_cols[0]
        n_col = numeric_cols[0]
        try:
            df_bar = df[[c_col, n_col]].dropna().sort_values(n_col, ascending=False).head(25)
            orientation = "h" if len(df_bar) > 10 else "v"
            if orientation == "h":
                fig = px.bar(df_bar, x=n_col, y=c_col, orientation="h")
            else:
                fig = px.bar(df_bar, x=c_col, y=n_col)
            fig.update_layout(margin=dict(t=30, b=30))
            figures.append((f"{n_col} by {c_col}", fig))
            print(f"[figure_builder] Created bar chart: {n_col} by {c_col}")
        except Exception as e:
            print(f"[figure_builder] Bar chart failed: {e}")

    # ── Scatter: two numeric columns ─────────────────────────────────────
    if len(numeric_cols) >= 2 and not figures:
        x_col, y_col = numeric_cols[0], numeric_cols[1]
        color_col = categorical_cols[0] if categorical_cols else None
        try:
            fig = px.scatter(df, x=x_col, y=y_col, color=color_col, opacity=0.7)
            fig.update_layout(margin=dict(t=30, b=30))
            figures.append((f"{y_col} vs {x_col}", fig))
            print(f"[figure_builder] Created scatter chart: {y_col} vs {x_col}")
        except Exception as e:
            print(f"[figure_builder] Scatter chart failed: {e}")

    # ── Value-count bar: single categorical column (e.g. COUNT(*) results) ──
    if not figures and categorical_cols and not numeric_cols:
        c_col = categorical_cols[0]
        try:
            counts = df[c_col].value_counts().head(25).reset_index()
            counts.columns = [c_col, "count"]
            orientation = "h" if len(counts) > 10 else "v"
            if orientation == "h":
                fig = px.bar(counts, x="count", y=c_col, orientation="h")
            else:
                fig = px.bar(counts, x=c_col, y="count")
            fig.update_layout(margin=dict(t=30, b=30))
            figures.append((f"Count by {c_col}", fig))
            print(f"[figure_builder] Created value-count bar: {c_col}")
        except Exception as e:
            print(f"[figure_builder] Value-count bar failed: {e}")

    # ── Single numeric: histogram ────────────────────────────────────────
    if not figures and len(numeric_cols) == 1 and len(df) > 1:
        n_col = numeric_cols[0]
        try:
            fig = px.histogram(df, x=n_col, nbins=min(30, len(df)))
            fig.update_layout(margin=dict(t=30, b=30))
            figures.append((f"Distribution of {n_col}", fig))
            print(f"[figure_builder] Created histogram: {n_col}")
        except Exception as e:
            print(f"[figure_builder] Histogram failed: {e}")

    return figures


def _coerce_numeric(df: pd.DataFrame) -> pd.DataFrame:
    """Tries to convert string columns that look numeric to float."""
    for col in df.columns:
        if not pd.api.types.is_numeric_dtype(df[col]):
            converted = pd.to_numeric(df[col], errors="coerce")
            # Only coerce if at least 80% of non-null values converted successfully
            non_null = df[col].notna().sum()
            if non_null > 0 and converted.notna().sum() / non_null >= 0.8:
                df = df.copy()
                df[col] = converted
    return df


def _detect_date_columns(df: pd.DataFrame) -> list[str]:
    """Returns column names that look like dates/times."""
    date_cols = []
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            date_cols.append(col)
            continue
        if df[col].dtype == object:
            # Heuristic: column name contains date/time keywords
            name_lower = col.lower()
            if any(kw in name_lower for kw in ("date", "time", "year", "month", "day", "ts", "created", "updated")):
                try:
                    pd.to_datetime(df[col].dropna().head(5), infer_datetime_format=True)
                    date_cols.append(col)
                except Exception:
                    pass
    return date_cols
