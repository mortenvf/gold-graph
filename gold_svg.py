#!/usr/bin/env python3
import argparse
import datetime as dt
import json
import math
import sys
import urllib.error
import urllib.request


API_URL = (
    "https://api.nordiskguld.dk/public/v2/metalprices/historical"
    "?metal=gold&currency=dkk&weightUnit=g&frequency=monthly"
)


def _parse_ymd(s: str) -> dt.date:
    try:
        return dt.date.fromisoformat(s)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"Invalid date {s!r}. Use YYYY-MM-DD.") from e


def _parse_api_date(s: str) -> dt.datetime:
    # API returns e.g. "2026-04-01T00:00:00.000Z"
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        return dt.datetime.fromisoformat(s)
    except ValueError:
        # Fallback: accept YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS
        try:
            d = dt.date.fromisoformat(s[:10])
            return dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc)
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"Unparseable API date: {s!r}") from e


def fetch_prices(url: str) -> list[tuple[dt.datetime, float]]:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "gold_svg.py (+https://cursor.sh)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
    except urllib.error.URLError as e:
        raise RuntimeError(f"Failed to fetch data from API: {e}") from e
    return parse_prices_json(raw)


def parse_prices_json(raw: bytes | str) -> list[tuple[dt.datetime, float]]:
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError("API did not return valid JSON.") from e

    if not isinstance(payload, list):
        raise RuntimeError(f"Unexpected JSON shape: expected list, got {type(payload).__name__}")

    points: list[tuple[dt.datetime, float]] = []
    for i, item in enumerate(payload):
        if not isinstance(item, dict):
            continue
        if "date" not in item or "price" not in item:
            continue
        try:
            t = _parse_api_date(str(item["date"]))
            p = float(item["price"])
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"Bad item at index {i}: {item!r}") from e
        if not math.isfinite(p):
            continue
        points.append((t, p))

    if not points:
        raise RuntimeError("No usable (date, price) points in API response.")

    # Sort ascending and dedupe identical timestamps (keep last).
    points.sort(key=lambda x: x[0])
    dedup: dict[dt.datetime, float] = {}
    for t, p in points:
        dedup[t] = p
    return sorted(dedup.items(), key=lambda x: x[0])


def filter_points(
    points: list[tuple[dt.datetime, float]],
    start: dt.date | None,
    end: dt.date | None,
) -> list[tuple[dt.datetime, float]]:
    out: list[tuple[dt.datetime, float]] = []
    for t, p in points:
        d = t.date()
        if start is not None and d < start:
            continue
        if end is not None and d > end:
            continue
        out.append((t, p))
    return out


def svg_path(
    points: list[tuple[dt.datetime, float]],
    width: int,
    height: int,
    pad: float,
    baseline: float,
) -> tuple[str, float]:
    ts = [p[0].timestamp() for p in points]
    ys = [p[1] for p in points]

    min_x, max_x = min(ts), max(ts)
    data_min_y, data_max_y = min(ys), max(ys)
    min_y = min(data_min_y, baseline)
    max_y = max(data_max_y, baseline)

    w = max(1.0, float(width))
    h = max(1.0, float(height))

    inner_w = max(1e-9, w - 2.0 * pad)
    inner_h = max(1e-9, h - 2.0 * pad)

    if max_x == min_x:
        def x_map(_: float) -> float:
            return pad + inner_w / 2.0
    else:
        def x_map(x: float) -> float:
            return pad + (x - min_x) / (max_x - min_x) * inner_w

    if max_y == min_y:
        def y_map(_: float) -> float:
            return pad + inner_h / 2.0
    else:
        def y_map(y: float) -> float:
            # invert so larger price plots higher (smaller y)
            return pad + (max_y - y) / (max_y - min_y) * inner_h

    coords = [(x_map(x), y_map(y)) for x, y in zip(ts, ys)]
    d = [f"M {coords[0][0]:.3f} {coords[0][1]:.3f}"]
    for x, y in coords[1:]:
        d.append(f"L {x:.3f} {y:.3f}")
    return " ".join(d), y_map(baseline)


def render_svg(
    path_d: str,
    axis_y: float,
    width: int,
    height: int,
    stroke_width: float,
    pad: float,
) -> str:
    w = max(1, int(width))
    h = max(1, int(height))
    sw = max(0.1, float(stroke_width))
    p = float(pad)
    x1 = max(0.0, min(float(w), p))
    x2 = max(0.0, min(float(w), float(w) - p))
    y = max(0.0, min(float(h), float(axis_y)))
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">\n'
        f'  <line x1="{x1:.3f}" y1="{y:.3f}" x2="{x2:.3f}" y2="{y:.3f}" stroke="#000" stroke-width="1"/>\n'
        f'  <path d="{path_d}" fill="none" stroke="#000" stroke-width="{sw}"/>\n'
        "</svg>\n"
    )


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="gold_svg.py",
        description="Download monthly gold prices and render a single-line SVG graph (no axes/labels).",
    )
    ap.add_argument("--start", type=_parse_ymd, default=None, help="Start date (inclusive), YYYY-MM-DD.")
    ap.add_argument("--end", type=_parse_ymd, default=None, help="End date (inclusive), YYYY-MM-DD.")
    ap.add_argument("--output", "-o", required=True, help="Output SVG path.")
    ap.add_argument("--width", type=int, default=800, help="SVG width in pixels.")
    ap.add_argument("--height", type=int, default=400, help="SVG height in pixels.")
    ap.add_argument("--stroke-width", type=float, default=2.0, help="Line stroke width.")
    ap.add_argument("--pad", type=float, default=10.0, help="Padding (px) around the line.")
    ap.add_argument(
        "--baseline",
        type=float,
        default=0.0,
        help="Y-value for x-axis baseline (default: 0). Included in scaling.",
    )
    ap.add_argument("--url", default=API_URL, help="Override API URL (advanced).")
    ap.add_argument(
        "--input-json",
        default=None,
        help="Read JSON from file path (or '-' for stdin) instead of fetching from the network.",
    )
    args = ap.parse_args(argv)

    if args.start and args.end and args.start > args.end:
        ap.error("--start must be <= --end")

    if args.input_json:
        if args.input_json == "-":
            raw = sys.stdin.read()
        else:
            with open(args.input_json, "r", encoding="utf-8") as f:
                raw = f.read()
        points = parse_prices_json(raw)
    else:
        points = fetch_prices(args.url)
    points = filter_points(points, args.start, args.end)
    if len(points) < 2:
        raise SystemExit("Not enough points after filtering to draw a line (need at least 2).")

    d, axis_y = svg_path(points, args.width, args.height, args.pad, args.baseline)
    svg = render_svg(d, axis_y, args.width, args.height, args.stroke_width, args.pad)

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(svg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
