#!/usr/bin/env python3
"""Radar charts for Method_vectorRag: combined figure with one panel per dataset plus average.

Datasets: Diabetes, Thyroid, Parathyroid, Pituitary, Adrenal, Reproductive, UKEU,
plus a panel with the mean accuracy across those seven per (LLM, embedding).

Reads ``overall_accuracy`` from evaluation JSON files under
``evaluate/Method_vectorRag/<llm>/<embedding>/LLM/Cosine_C512_100/`` only.

Improvements:
- Uses a zoomed radial range instead of always starting at 0, making crowded 60–80% values readable.
- Embedding names are shown around each radar; radial ring labels are hidden.
- Each embedding axis shows only the best model's accuracy, offset along the spoke.
- The original colour palette is preserved but lightened slightly.

Example::

    python evaluate/plot_vector_rag_radar2.py --out-dir evaluate/figures

By default writes two layouts (``--layout both``):

- ``vector_rag_radar_combined.png`` — 4 rows × 2 columns
- ``vector_rag_radar_combined_2x4.png`` — 2 rows × 4 columns

Optional fixed range::

    python evaluate/plot_vector_rag_radar2.py --y-min 45 --y-max 80 --layout 2x4
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from typing import Iterable, Sequence

import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import numpy as np

from endorag.analysis.artifact_resolver import get_resolver
from endorag.analysis.cli_args import add_paper_analysis_args, resolve_analysis_context

# Qwen-style benchmark chart palette: orange, pink, green, blue, then extras.
# These are blended toward white below to make the final colours lighter.
BASE_SERIES_COLORS = ("#E07C3C", "#D65C9C", "#2F7D4B", "#5B9BD5", "#C9A227", "#6B5B95")
COLOR_LIGHTEN_AMOUNT = 0.1

SERIES_FILL_ALPHA = 0.20
VALUE_TEXT_COLOR = "#1A1A1A"

AXIS_LABEL_PAD_PT = 20
COMBINED_THETA_LABEL_FONTSIZE = 22
COMBINED_VALUE_LABEL_FONTSIZE = 17
COMBINED_PANEL_TITLE_FONTSIZE = 24
COMBINED_LEGEND_FONTSIZE = 28
COMBINED_RADIAL_TICK_FONTSIZE = 18
COMBINED_MARKER_SIZE = 8
COMBINED_LINE_WIDTH = 2.4
# Inches per subplot cell (2 columns × 4 rows for 8 panels).
COMBINED_PANEL_WIDTH = 11.0
COMBINED_PANEL_HEIGHT = 10.0
COMBINED_GRID_WSPACE = 0.22
COMBINED_GRID_HSPACE = 0.32

# Zoomed radial scale settings.
RADIAL_MIN_PADDING_PCT = 5.0
RADIAL_MAX_PADDING_PCT = 2.0
RADIAL_TICK_STEP = 1.0
RADIAL_RING_COUNT = 6

# Value label callout settings (data-space offset along each spoke).
VALUE_LABEL_OFFSET_PCT = 3.0
VALUE_LABEL_OUTER_PAD_FRAC = 0.055
VALUE_LABEL_FONT_SIZE = 14

# (embedding subdirectory name, short axis label)
# DEFAULT_EMBEDDINGS: Sequence[tuple[str, str]] = (
#     ("embeddinggemma", "embedding gemma"),
#     ("bge-m3:latest", "bge-m3"),
#     ("text-embedding-3-large", "text-embedding-3-large"),
#     ("nomic-embed-text:latest", "nomic-embed-text"),
#     ("qwen3-embedding:8b", "qwen3-embedding:8b"),
# )

DEFAULT_EMBEDDINGS: Sequence[tuple[str, str]] = (
    ("embeddinggemma", "Embedding\nGemma"),
    ("bge-m3:latest", "BGE\n-M3"),
    ("text-embedding-3-large", "text-\nembedding-\n3-large"),
    ("nomic-embed-text:latest", "nomic-\nembed-text"),
    ("qwen3-embedding:8b", "Qwen3-\nEmbedding-\n8B"),
)

# (LLM subdirectory name, legend label)
DEFAULT_LLMS: Sequence[tuple[str, str]] = (
    ("gemma4:31b-cloud", "Gemma4-31b"),
    ("minimax-m2.7:cloud", "MiniMax-M2.7"),
    ("mistral-small3.2:24b", "Mistral-small3.2-24b"),
    ("nemotron-3-nano:30b-cloud", "Nemotron 3 Nano 30b"),
)

# (file basename prefix, subplot title)
DATASETS: Sequence[tuple[str, str]] = (
    ("MCQs_book", "Diabetes and lipid metabolism"),
    ("ThyroidGland_dataset", "Thyroid gland"),
    ("ParathyroidGlandAndBoneDisease_dataset", "Parathyroid gland and bone disease"),
    ("PituitaryGlandAndHypothalamus_dataset", "Pituitary and hypothalamus"),
    ("AdrenalGlands_dataset", "Adrenal glands"),
    ("ReproductiveEndocrinology_dataset", "Reproductive endocrinology"),
    ("UKEU", "UKEU"),
)

AVERAGE_PANEL_TITLE = "Macro-average (7 datasets)"

# Alternate dataset stems used in saved eval filenames (see run_*_eval_vector.sh).
DATASET_PREFIX_ALIASES: dict[str, tuple[str, ...]] = {
    "MCQs_book": ("MCQs_book", "MCQs_sample_questions2015_full"),
    "UKEU": ("UKEU", "UKEU_dataset"),
}


@dataclass(frozen=True)
class LayoutConfig:
    ncols: int
    panel_width: float
    panel_height: float
    font_scale: float
    hspace: float
    wspace: float
    show_value_labels: bool
    value_label_font_scale: float
    # Show radial % ticks only on this column (0-based); None = every panel.
    radial_ticks_col: int | None
    axis_label_pad_pt: float
    # Fixed space below each radar, in inches (relative to subplot cell height).
    caption_gap_in: float
    caption_height_in: float


LAYOUT_PRESETS: dict[str, LayoutConfig] = {
    # 4 rows × 2 columns (tall figure)
    "4x2": LayoutConfig(
        ncols=2,
        panel_width=11.0,
        panel_height=10.0,
        font_scale=1.0,
        hspace=0.32,
        wspace=0.22,
        show_value_labels=True,
        value_label_font_scale=1.0,
        radial_ticks_col=None,
        axis_label_pad_pt=AXIS_LABEL_PAD_PT,
        caption_gap_in=0.50,
        caption_height_in=0.55,
    ),
    # 2 rows × 4 columns — large fonts so text stays readable when the wide
    # figure is scaled to \textwidth in LaTeX (~6–7 in).
    "2x4": LayoutConfig(
        ncols=4,
        panel_width=12.0,
        panel_height=12.0,
        font_scale=1.55,
        hspace=0.70,
        wspace=0.24,
        show_value_labels=True,
        value_label_font_scale=1.15,
        radial_ticks_col=None,
        axis_label_pad_pt=26.0,
        caption_gap_in=1.15,
        caption_height_in=0.75,
    ),
}


def _lighten_color(hex_color: str, amount: float) -> str:
    """Blend a hex colour toward white. amount=0 keeps original; amount=1 gives white."""
    rgb = np.array(mcolors.to_rgb(hex_color), dtype=float)
    rgb = rgb + (1.0 - rgb) * amount
    return mcolors.to_hex(np.clip(rgb, 0.0, 1.0))


SERIES_COLORS = tuple(_lighten_color(c, COLOR_LIGHTEN_AMOUNT) for c in BASE_SERIES_COLORS)


def _slug_name(name: str) -> str:
    """Match run_*_eval_vector.sh: tr '/:' '__'."""
    return name.replace("/", "_").replace(":", "_")


def _embed_suffixes(embed_dir: str) -> tuple[str, ...]:
    """Accepted filename endings for one embedding model (literal and slugged)."""
    slug = _slug_name(embed_dir)
    return tuple(dict.fromkeys((f"_{embed_dir}_1.json", f"_{slug}_1.json")))


def _dataset_prefixes(dataset_prefix: str) -> tuple[str, ...]:
    aliases = DATASET_PREFIX_ALIASES.get(dataset_prefix, (dataset_prefix,))
    return tuple(dict.fromkeys(aliases))


def _find_eval_json(llm_dir: str, embed_dir: str, dataset_prefix: str) -> str | None:
    """Return path to the *_1.json result in LLM/Cosine_C512_100 for one (llm, embed, dataset)."""
    base = str(get_resolver().vector_embed_base(llm_dir, embed_dir) / "LLM" / "Cosine_C512_100")
    if not os.path.isdir(base):
        return None

    suffixes = _embed_suffixes(embed_dir)
    prefixes = _dataset_prefixes(dataset_prefix)

    matches: list[str] = []
    for name in os.listdir(base):
        if name.startswith("rerank_"):
            continue
        if not name.endswith("_1.json"):
            continue
        if not any(name.endswith(suffix) for suffix in suffixes):
            continue
        if not any(name.startswith(prefix) for prefix in prefixes):
            continue
        if "diabetesVectorTool512_100" not in name:
            continue
        matches.append(os.path.join(base, name))

    if not matches:
        return None

    matches.sort()
    return matches[0]


def _load_overall_accuracy(path: str) -> float:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return float(data["summary"]["overall_accuracy"])


def _collect_matrix(
    llms: Sequence[tuple[str, str]],
    embeddings: Sequence[tuple[str, str]],
    dataset_prefix: str,
) -> tuple[np.ndarray, list[str | None]]:
    """Shape: (n_llms, n_embeddings). Missing or bad files become NaN."""
    n_l, n_e = len(llms), len(embeddings)
    mat = np.full((n_l, n_e), np.nan, dtype=float)
    missing: list[str | None] = []

    for i, (llm_dir, _) in enumerate(llms):
        for j, (embed_dir, _) in enumerate(embeddings):
            p = _find_eval_json(llm_dir, embed_dir, dataset_prefix)

            if p is None:
                missing.append(f"{llm_dir} + {embed_dir} + {dataset_prefix}")
                continue

            try:
                mat[i, j] = _load_overall_accuracy(p) * 100.0
            except (KeyError, OSError, json.JSONDecodeError, TypeError, ValueError):
                missing.append(p)

    return mat, missing


def _average_matrix(mats: Sequence[np.ndarray]) -> np.ndarray:
    """Mean accuracy across datasets for each (llm, embedding); NaN when all missing."""
    if not mats:
        return np.empty((0, 0), dtype=float)
    stacked = np.stack(mats, axis=0)
    with np.errstate(invalid="ignore"):
        return np.nanmean(stacked, axis=0)


def _radial_limits(
    mat: np.ndarray,
    y_min_override: float | None,
    y_max_override: float | None,
) -> tuple[float, float]:
    """Choose a zoomed radial range so clustered 60–80% values are readable."""
    finite = mat[np.isfinite(mat)]
    if finite.size == 0:
        return 0.0, 100.0

    raw_min = float(np.min(finite))
    raw_max = float(np.max(finite))

    if y_min_override is None:
        y_min = np.floor((raw_min - RADIAL_MIN_PADDING_PCT) / RADIAL_TICK_STEP) * RADIAL_TICK_STEP
        y_min = max(0.0, y_min)
    else:
        y_min = float(y_min_override)

    if y_max_override is None:
        y_max = np.ceil((raw_max + RADIAL_MAX_PADDING_PCT) / RADIAL_TICK_STEP) * RADIAL_TICK_STEP
        y_max = min(100.0, y_max)
    else:
        y_max = float(y_max_override)

    if y_max <= y_min:
        y_max = y_min + RADIAL_TICK_STEP

    return float(y_min), float(y_max)


def _style_theta_labels(
    ax: plt.Axes,
    angles: np.ndarray,
    *,
    label_fontsize: float = 20,
    axis_label_pad_pt: float = AXIS_LABEL_PAD_PT,
) -> None:
    """Move embedding labels away from the radar area and rotate them tangentially."""
    ax.tick_params(axis="x", pad=axis_label_pad_pt)

    for label, theta in zip(ax.get_xticklabels(), angles):
        display_theta = ax.get_theta_offset() + ax.get_theta_direction() * theta
        display_deg = np.degrees(display_theta)

        rotation = display_deg - 90.0
        if rotation < -90.0:
            rotation += 180.0
        elif rotation > 90.0:
            rotation -= 180.0

        x = np.cos(display_theta)
        y = np.sin(display_theta)

        label.set_rotation(rotation)
        label.set_rotation_mode("anchor")
        label.set_clip_on(True)
        label.set_fontsize(label_fontsize)

        if x > 0.20:
            label.set_ha("left")
        elif x < -0.20:
            label.set_ha("right")
        else:
            label.set_ha("center")

        if y > 0.20:
            label.set_va("bottom")
        elif y < -0.20:
            label.set_va("top")
        else:
            label.set_va("center")


# Per embedding axis (0=Gemma … 4=Qwen3 8B): anchor text like the working Qwen spoke.
_VALUE_LABEL_ANCHOR: tuple[tuple[str, str], ...] = (
    ("center", "bottom"),  # Embedding Gemma — inward along top spoke
    ("left", "center"),    # BGE-M3 — upper right
    ("left", "center"),    # Text embed-3-large — lower right
    ("right", "center"),   # Nomic embed-text — lower left (same as Qwen)
    ("right", "center"),   # Qwen3 8B — upper left
)
# Outward multiplier per axis (lower = label closer to the vertex / visually lower).
_VALUE_LABEL_OUTWARD_MUL: tuple[float, ...] = (
    0.65,  # 0 Embedding Gemma
    0.72,  # 1 BGE-M3
    1.15,  # 2 Text embed-3-large
    1.15,  # 3 Nomic embed-text
    0.72,  # 4 Qwen3 8B
)


def _value_label_data_position(
    ax: plt.Axes,
    angle: float,
    value: float,
    y_min: float,
    y_max: float,
    axis_idx: int,
    font_scale: float,
) -> tuple[float, float, str, str]:
    """Outward along the spoke; top (Gemma) tucks inward to clear the axis title."""
    radial_span = max(y_max - y_min, 1e-6)
    offset = (
        VALUE_LABEL_OFFSET_PCT
        * (0.85 + 0.15 * font_scale)
    )
    outward_mul = _VALUE_LABEL_OUTWARD_MUL[axis_idx % len(_VALUE_LABEL_OUTWARD_MUL)]

    if axis_idx == 0:
        # Slightly above the vertex, but capped below the axis title.
        outer_cap = y_max - 0.015 * radial_span
        label_r = min(value + offset * outward_mul, outer_cap)
        return angle, label_r, "center", "bottom"

    max_r = y_max + VALUE_LABEL_OUTER_PAD_FRAC * radial_span
    label_r = min(value + offset * outward_mul, max_r)
    ha, va = _VALUE_LABEL_ANCHOR[axis_idx % len(_VALUE_LABEL_ANCHOR)]
    return angle, label_r, ha, va


def _annotate_value_labels(
    ax: plt.Axes,
    values: np.ndarray,
    angles: np.ndarray,
    y_min: float,
    y_max: float,
    *,
    value_fontsize: float = VALUE_LABEL_FONT_SIZE,
    compact: bool = False,
) -> None:
    """Label only the best (highest accuracy) model on each embedding axis."""
    n_series, n_axes = values.shape
    font_scale = value_fontsize / VALUE_LABEL_FONT_SIZE

    for j in range(n_axes):
        raw_points = [
            (i, float(values[i, j]))
            for i in range(n_series)
            if np.isfinite(values[i, j])
        ]

        if not raw_points:
            continue

        series_idx, value = max(raw_points, key=lambda item: item[1])
        label_text = f"{value:.1f}%"
        label_theta, label_r, ha, va = _value_label_data_position(
            ax,
            angles[j],
            value,
            y_min,
            y_max,
            j,
            font_scale,
        )

        ax.annotate(
            label_text,
            xy=(angles[j], value),
            xytext=(label_theta, label_r),
            textcoords="data",
            ha=ha,
            va=va,
            fontsize=value_fontsize,
            color=VALUE_TEXT_COLOR,
            clip_on=False,
            zorder=30 + series_idx,
            bbox=dict(
                boxstyle="round,pad=0.12",
                facecolor="white",
                edgecolor="none",
                alpha=0.92,
            ),
        )

def _radar_ax(
    ax: plt.Axes,
    values: np.ndarray,
    labels: Sequence[str],
    series_names: Sequence[str],
    title: str,
    y_min: float,
    y_max: float,
    *,
    theta_label_fontsize: float = 20,
    axis_label_pad_pt: float = AXIS_LABEL_PAD_PT,
    compact_value_labels: bool = False,
    show_value_labels: bool = True,
    value_label_fontsize: float = VALUE_LABEL_FONT_SIZE,
    radial_tick_fontsize: float = 14,
    show_radial_ticks: bool = True,
    marker_size: float = 6,
    line_width: float = 2.0,
) -> None:
    n = len(labels)
    angles = np.linspace(0, 2 * np.pi, n, endpoint=False)
    angles_closed = np.concatenate([angles, angles[:1]])

    ax.set_theta_offset(np.pi / 2)
    ax.set_theta_direction(-1)

    ax.set_thetagrids(np.degrees(angles), labels)
    _style_theta_labels(
        ax,
        angles,
        label_fontsize=theta_label_fontsize,
        axis_label_pad_pt=axis_label_pad_pt,
    )

    ax.set_ylim(y_min, y_max)

    rings = np.linspace(y_min, y_max, RADIAL_RING_COUNT)
    ax.set_yticks(rings)
    ax.set_yticklabels([])

    # Uncomment to show dataset title.
    # ax.set_title(title, pad=24, fontsize=12, fontweight="medium")

    ax.grid(True, color="#B0B0B0", linestyle="-", linewidth=0.6, alpha=0.80)
    ax.spines["polar"].set_visible(False)
    ax.set_axisbelow(True)

    for i, name in enumerate(series_names):
        row = values[i]
        if np.all(np.isnan(row)):
            continue

        color = SERIES_COLORS[i % len(SERIES_COLORS)]
        row_closed = np.concatenate([row, row[:1]])

        ax.fill(
            angles_closed,
            row_closed,
            alpha=SERIES_FILL_ALPHA,
            color=color,
            zorder=2 + i * 0.01,
        )

        ax.plot(
            angles_closed,
            row_closed,
            "o-",
            linewidth=line_width,
            markersize=marker_size,
            markeredgecolor="white",
            markeredgewidth=0.9,
            label=name,
            color=color,
            zorder=3 + i * 0.01,
        )

    if show_value_labels:
        _annotate_value_labels(
            ax,
            values,
            angles,
            y_min,
            y_max,
            value_fontsize=value_label_fontsize,
            compact=compact_value_labels,
        )


def _place_panel_caption(
    fig: plt.Figure,
    ax: plt.Axes,
    title: str,
    *,
    caption_gap_in: float,
    caption_height_in: float,
    fontsize: float,
) -> None:
    """Draw the caption in a fixed band *below* the subplot cell (radar keeps full cell)."""
    pos = ax.get_position()
    fig_h = fig.get_figheight()
    gap_norm = caption_gap_in / fig_h
    cap_norm = caption_height_in / fig_h
    # Band layout below the cell: [gap][caption text in lower part of caption band]
    text_y = pos.y0 - gap_norm - cap_norm * 0.72
    fig.text(
        pos.x0 + 0.5 * pos.width,
        max(text_y, 0.015),
        title,
        ha="center",
        va="center",
        fontsize=fontsize,
        fontweight="semibold",
        transform=fig.transFigure,
        clip_on=False,
        zorder=10,
    )


def _render_combined_figure(
    out_path: str,
    dataset_results: Sequence[tuple[str, np.ndarray]],
    embed_labels: Sequence[str],
    series_names: Sequence[str],
    layout: LayoutConfig,
    y_min_override: float | None,
    y_max_override: float | None,
) -> None:
    n_panels = len(dataset_results)
    ncols = layout.ncols
    nrows = int(np.ceil(n_panels / ncols))
    fig, axes = plt.subplots(
        nrows,
        ncols,
        figsize=(layout.panel_width * ncols, layout.panel_height * nrows),
        subplot_kw=dict(projection="polar"),
    )
    fig.patch.set_facecolor("white")
    axes_flat = np.atleast_1d(axes).ravel()

    font_scale = layout.font_scale
    theta_fs = COMBINED_THETA_LABEL_FONTSIZE * font_scale
    value_fs = (
        COMBINED_VALUE_LABEL_FONTSIZE
        * font_scale
        * layout.value_label_font_scale
    )
    radial_fs = COMBINED_RADIAL_TICK_FONTSIZE * font_scale
    title_fs = COMBINED_PANEL_TITLE_FONTSIZE * font_scale
    legend_fs = COMBINED_LEGEND_FONTSIZE * font_scale
    marker_size = COMBINED_MARKER_SIZE * font_scale

    for panel_idx, (ax, (dataset_title, mat)) in enumerate(
        zip(axes_flat, dataset_results)
    ):
        col = panel_idx % ncols
        show_radial = (
            layout.radial_ticks_col is None or col == layout.radial_ticks_col
        )
        y_min, y_max = _radial_limits(
            mat,
            y_min_override=y_min_override,
            y_max_override=y_max_override,
        )
        _radar_ax(
            ax,
            mat,
            embed_labels,
            series_names,
            dataset_title,
            y_min=y_min,
            y_max=y_max,
            theta_label_fontsize=theta_fs,
            axis_label_pad_pt=layout.axis_label_pad_pt,
            compact_value_labels=True,
            show_value_labels=layout.show_value_labels,
            value_label_fontsize=value_fs,
            radial_tick_fontsize=radial_fs,
            show_radial_ticks=show_radial,
            marker_size=marker_size,
            line_width=COMBINED_LINE_WIDTH,
        )

    for ax in axes_flat[n_panels:]:
        ax.set_visible(False)

    handles, labels = axes_flat[0].get_legend_handles_labels()

    fig_h = fig.get_figheight()
    caption_reserve_norm = (layout.caption_gap_in + layout.caption_height_in) / fig_h
    bottom_margin = max(0.10, caption_reserve_norm * 1.35 + 0.04)
    # Reserve headroom above the top row for the legend.
    top_margin = 0.86 if ncols >= 4 else 0.91

    fig.subplots_adjust(
        left=0.04,
        right=0.98,
        top=top_margin,
        bottom=bottom_margin,
        wspace=layout.wspace,
        hspace=layout.hspace,
    )

    leg = fig.legend(
        handles,
        labels,
        loc="upper center",
        ncol=min(len(series_names), 4),
        bbox_to_anchor=(0.5, 0.995),
        fontsize=legend_fs,
        frameon=True,
        fancybox=False,
        edgecolor="#CCCCCC",
        facecolor="white",
    )

    for legline, legtext in zip(leg.get_lines(), leg.get_texts()):
        legline.set_linewidth(10.0)
        legline.set_marker("o")
        legline.set_markersize(9)
        legtext.set_fontsize(legend_fs)

    for ax, (dataset_title, _) in zip(axes_flat, dataset_results):
        _place_panel_caption(
            fig,
            ax,
            dataset_title,
            caption_gap_in=layout.caption_gap_in,
            caption_height_in=layout.caption_height_in,
            fontsize=title_fs,
        )

    pad_inches = 0.22 if ncols >= 4 else 0.35
    fig.savefig(out_path, dpi=300, bbox_inches="tight", pad_inches=pad_inches)
    plt.close(fig)
    print(f"Wrote {out_path}")


def plot_all(
    out_dir: str,
    llms: Sequence[tuple[str, str]],
    embeddings: Sequence[tuple[str, str]],
    y_min_override: float | None,
    y_max_override: float | None,
    layouts: Sequence[str] = ("4x2", "2x4"),
) -> None:
    os.makedirs(out_dir, exist_ok=True)

    embed_labels = [e[1] for e in embeddings]
    series_names = [l[1] for l in llms]
    dataset_mats: list[np.ndarray] = []

    for dataset_prefix, _ in DATASETS:
        mat, missing = _collect_matrix(llms, embeddings, dataset_prefix)
        dataset_mats.append(mat)

        if missing:
            uniq = sorted({m for m in missing if m})
            print(f"[warn] {dataset_prefix}: {len(uniq)} missing/bad paths (showing up to 8):")
            for m in uniq[:8]:
                print(f"         {m}")

    avg_mat = _average_matrix(dataset_mats)
    dataset_results: list[tuple[str, np.ndarray]] = [
        (title, mat) for (_, title), mat in zip(DATASETS, dataset_mats)
    ]
    dataset_results.append((AVERAGE_PANEL_TITLE, avg_mat))

    out_names = {
        "4x2": "vector_rag_radar_combined.png",
        "2x4": "vector_rag_radar_combined_2x4.png",
    }

    for layout_key in layouts:
        if layout_key not in LAYOUT_PRESETS:
            raise ValueError(
                f"Unknown layout {layout_key!r}; choose from {sorted(LAYOUT_PRESETS)}"
            )
        layout = LAYOUT_PRESETS[layout_key]
        out_path = os.path.join(out_dir, out_names[layout_key])
        _render_combined_figure(
            out_path,
            dataset_results,
            embed_labels,
            series_names,
            layout,
            y_min_override=y_min_override,
            y_max_override=y_max_override,
        )


def _parse_pairs(spec: Iterable[str]) -> list[tuple[str, str]]:
    """Parse items as ``subdir==Legend label`` or ``subdir``."""
    out: list[tuple[str, str]] = []

    for s in spec:
        s = s.strip()

        if "==" in s:
            d, lab = s.split("==", 1)
            out.append((d.strip(), lab.strip()))
        else:
            out.append((s, s))

    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)

    add_paper_analysis_args(p, default_output_dir="results/analysis_exports/figures")

    p.add_argument(
        "--y-min",
        type=float,
        default=None,
        metavar="PCT",
        help="Radial axis minimum in percent. Default: slightly below the lowest value.",
    )

    p.add_argument(
        "--y-max",
        type=float,
        default=None,
        metavar="PCT",
        help="Radial axis maximum in percent. Default: slightly above the highest value.",
    )

    p.add_argument(
        "--llm",
        action="append",
        dest="llms",
        metavar="DIR==LABEL",
        help="Override/add LLM. Repeatable. Example: --llm phi4:latest==Phi-4",
    )

    p.add_argument(
        "--embedding",
        action="append",
        dest="embeddings",
        metavar="DIR==LABEL",
        help="Override embedding axis. Repeatable. Example: --embedding qwen3-embedding:8b==Qwen",
    )

    p.add_argument(
        "--layout",
        choices=["4x2", "2x4", "both"],
        default="both",
        help=(
            "Panel grid: 4x2 = 4 rows × 2 cols (tall), 2x4 = 2 rows × 4 cols (wide). "
            "Default both writes vector_rag_radar_combined.png and vector_rag_radar_combined_2x4.png."
        ),
    )

    args = p.parse_args()
    _repo_root, _manifest, output_dir = resolve_analysis_context(args)

    llms = _parse_pairs(args.llms) if args.llms else list(DEFAULT_LLMS)
    embeddings = _parse_pairs(args.embeddings) if args.embeddings else list(DEFAULT_EMBEDDINGS)

    if args.layout == "both":
        layouts = ("4x2", "2x4")
    else:
        layouts = (args.layout,)

    plot_all(
        str(output_dir),
        llms,
        embeddings,
        y_min_override=args.y_min,
        y_max_override=args.y_max,
        layouts=layouts,
    )


if __name__ == "__main__":
    main()