from __future__ import annotations

import base64
import io
import logging
from typing import Any

import matplotlib

matplotlib.use("Agg")  # headless
import matplotlib.pyplot as plt  # noqa: E402

log = logging.getLogger("igna.agent.figure_builder")


class FigureBuilder:
    """Agent 5 — pure-Python matplotlib renderer; no LLM call."""

    name = "figure_builder"

    # Max data points rendered per chart. Beyond this the labels become unreadable
    # and matplotlib tries to draw hundreds of bars/points into a fixed canvas.
    MAX_PLOT_ROWS = 30

    def render(self, spec: dict[str, Any], rows: list[dict[str, Any]]) -> str | None:
        log.info("figure_builder ▶ | chart=%s x=%s y=%s group_by=%s rows=%d",
                 spec.get("chart"), spec.get("x"), spec.get("y"), spec.get("group_by"), len(rows))
        if not rows or not spec:
            log.info("figure_builder skipped | no rows or no spec")
            return None
        chart = (spec.get("chart") or "bar").lower()
        x = spec.get("x")
        y = spec.get("y")
        group_by = spec.get("group_by")
        title = spec.get("title") or ""
        if not x or not y or x not in rows[0] or y not in rows[0]:
            log.warning("figure_builder skipped | x/y not present in rows (x=%s y=%s cols=%s)", x, y, list(rows[0].keys()))
            return None

        if len(rows) > self.MAX_PLOT_ROWS:
            log.info("figure_builder capped rows %d → %d", len(rows), self.MAX_PLOT_ROWS)
            rows = rows[:self.MAX_PLOT_ROWS]
            title = f"{title} (top {self.MAX_PLOT_ROWS})".strip()

        fig, ax = plt.subplots(figsize=(8, 5))
        try:
            distinct_groups = len({r[group_by] for r in rows}) if group_by and group_by in rows[0] else 0
            if group_by and group_by in rows[0] and distinct_groups <= 10:
                groups: dict[Any, list[tuple[Any, Any]]] = {}
                for r in rows:
                    groups.setdefault(r[group_by], []).append((r[x], r[y]))
                if chart == "line":
                    for g, pts in groups.items():
                        pts_sorted = sorted(pts, key=lambda p: p[0])
                        ax.plot([p[0] for p in pts_sorted], [p[1] for p in pts_sorted], marker="o", label=str(g))
                    ax.legend()
                else:
                    import numpy as np

                    cats = sorted({p[0] for pts in groups.values() for p in pts}, key=lambda v: str(v))
                    width = 0.8 / max(1, len(groups))
                    for i, (g, pts) in enumerate(groups.items()):
                        lookup = dict(pts)
                        ys = [lookup.get(c, 0) for c in cats]
                        xs = np.arange(len(cats)) + i * width
                        ax.bar(xs, ys, width=width, label=str(g))
                    ax.set_xticks(np.arange(len(cats)) + (len(groups) - 1) * width / 2)
                    ax.set_xticklabels([str(c) for c in cats], rotation=30, ha="right")
                    ax.legend()
            else:
                xs = [r[x] for r in rows]
                ys = [r[y] for r in rows]
                if chart == "line":
                    pairs = sorted(zip(xs, ys), key=lambda p: p[0])
                    ax.plot([p[0] for p in pairs], [p[1] for p in pairs], marker="o")
                elif chart == "pie":
                    ax.pie(ys, labels=[str(v) for v in xs], autopct="%1.1f%%")
                else:
                    ax.bar([str(v) for v in xs], ys)
                    plt.xticks(rotation=45, ha="right", fontsize=8)
            ax.set_title(title)
            if chart != "pie":
                ax.set_xlabel(x)
                ax.set_ylabel(y)
            fig.tight_layout()
            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=120)
            buf.seek(0)
            png_bytes = buf.read()
            log.info("figure_builder ✓ | png=%d bytes", len(png_bytes))
            return base64.b64encode(png_bytes).decode("ascii")
        finally:
            plt.close(fig)
