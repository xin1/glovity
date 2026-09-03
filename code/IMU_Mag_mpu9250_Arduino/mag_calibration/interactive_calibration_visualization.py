"""Render an interactive raw/calibrated view from mag_calibration/data."""

import json
from pathlib import Path

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots


DATA_DIR = Path(__file__).resolve().parent / "data"


def main() -> None:
    raw = np.loadtxt(DATA_DIR / "magnetometer_data.csv", delimiter=",")
    params = json.loads((DATA_DIR / "calibration_matrix.json").read_text(encoding="utf-8"))
    offset = np.asarray(params["hard_iron_offset"])
    matrix = np.asarray(params["soft_iron_matrix"])
    calibrated = (raw - offset) @ matrix.T

    figure = make_subplots(
        rows=1,
        cols=2,
        specs=[[{"type": "scatter3d"}, {"type": "scatter3d"}]],
        subplot_titles=("Raw", "Calibrated — full 3x3"),
    )
    for column, values, name, color in (
        (1, raw, "Raw", "#2878b5"),
        (2, calibrated, "Calibrated", "#d64f4f"),
    ):
        figure.add_trace(
            go.Scatter3d(
                x=values[:, 0], y=values[:, 1], z=values[:, 2],
                mode="markers", name=name,
                marker={"size": 2, "opacity": 0.45, "color": color},
            ),
            row=1, col=column,
        )
    figure.update_scenes(aspectmode="data")
    figure.update_layout(title="Magnetometer ellipsoid calibration", height=760, width=1400)
    output = DATA_DIR / "calibration_interactive.html"
    figure.write_html(output, include_plotlyjs=True)
    print(f"已保存：{output}")


if __name__ == "__main__":
    main()
