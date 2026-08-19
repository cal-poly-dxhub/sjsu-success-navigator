"""Render one static map image per campus building, from OpenStreetMap tiles.

RUN BY HAND, NOT IN CI, and only when a coordinate in data/buildings.csv changes:

    python -m pip install -r scripts/requirements.txt
    python scripts/render_place_maps.py

It reads the buildings table out of data/buildings.csv - through app/places.py, so it sees the
same rows the location card resolves against and not a second reading of the file - and writes
frontend/public/places/<key>.webp, which is committed. The deployed site serves those images from its own origin, so a student
reading an answer makes no request to any third party - not on render, not on a press.

WHY NOT GOOGLE'S STATIC MAPS API, which returns exactly this picture in one request: it
requires an API key and a billing account, which this project deliberately does not have,
and its terms forbid storing what it returns - so "fetch once and commit" is precisely the
thing they prohibit. Map tiles carry no such restriction. The rendered OSM tile layer is
CC-BY-SA and the underlying data is ODbL; both permit redistribution with attribution, which
is drawn into the corner of every image below.

WHY THE STANDARD OSM STYLE, when CARTO serves the same data at zoom 20 with proper @2x
retina tiles and looks cleaner: their styles render campus buildings as anonymous blocks.
The standard style prints "Student Services Center" under the pin, and that label is the
reason the picture helps anybody. Sharpness lost, meaning kept.

IT IS A LIGHT, ONE-OFF FETCH BY DESIGN. Sixteen catalogue entries share five buildings, so a
full run pulls on the order of forty tiles once. That sits inside the tile usage policy;
a build step that re-fetched on every deploy would not, which is why this is not wired into
one.
"""

from __future__ import annotations

import math
import ssl
import sys
import time
import urllib.request
from io import BytesIO
from pathlib import Path

import certifi
from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "app"))

from places import CAMPUS_BUILDINGS  # noqa: E402

OUT_DIR = REPO / "frontend" / "public" / "places"

CTX = ssl.create_default_context(cafile=certifi.where())
# The tile usage policy asks for a real User-Agent that identifies the application.
USER_AGENT = (
    "sjsu-student-success-navigator/1.0 "
    "(campus place map render, run by hand; https://github.com/cal-poly-dxhub)"
)
TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
TILE_PX = 256

# 152 METRES OF GROUND, and every number here follows from that rather than being picked.
# tile.openstreetmap.org serves the standard layer up to z19 and 400s above it, so tightening
# the frame means cropping less ground: at z19's 0.2374 m/px, 640 px is 152 m, and the
# building is the subject instead of a speck beside a car park.
#
# 640 px is ALSO ENOUGH DENSITY, which reads as wrong until you check the card it goes in:
# the panel is capped at 32 rem (about 512 px) and is roughly 320 px on a phone, so this is
# above 1x on a desktop and about 2x on mobile. An earlier pass rendered 1000 px wide FOR
# retina, and all that extra width bought was more car park.
WIDTH, HEIGHT = 640, 480
ZOOM = 19

ATTRIBUTION = "© OpenStreetMap contributors"


def world_pixels(lat: float, lon: float, zoom: int) -> tuple[float, float]:
    """Slippy-map projection: lat/lon to pixel coordinates on the whole world at `zoom`."""
    n = 2.0**zoom
    x = (lon + 180.0) / 360.0 * n * TILE_PX
    y = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n * TILE_PX
    return x, y


def fetch_tile(z: int, x: int, y: int) -> Image.Image:
    request = urllib.request.Request(
        TILE_URL.format(z=z, x=x, y=y), headers={"User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(request, timeout=30, context=CTX) as response:
        return Image.open(BytesIO(response.read())).convert("RGB")


def render(lat: float, lon: float) -> Image.Image:
    """A WIDTH x HEIGHT image centred exactly on the point, with a pin on it."""
    centre_x, centre_y = world_pixels(lat, lon, ZOOM)
    left, top = centre_x - WIDTH / 2, centre_y - HEIGHT / 2

    x0, x1 = math.floor(left / TILE_PX), math.floor((left + WIDTH) / TILE_PX)
    y0, y1 = math.floor(top / TILE_PX), math.floor((top + HEIGHT) / TILE_PX)

    canvas = Image.new(
        "RGB", ((x1 - x0 + 1) * TILE_PX, (y1 - y0 + 1) * TILE_PX), "#e8eae2"
    )
    for tile_x in range(x0, x1 + 1):
        for tile_y in range(y0, y1 + 1):
            canvas.paste(
                fetch_tile(ZOOM, tile_x, tile_y),
                ((tile_x - x0) * TILE_PX, (tile_y - y0) * TILE_PX),
            )
            # Restraint rather than speed: the policy asks for light use, not a burst.
            time.sleep(0.12)

    offset_x, offset_y = left - x0 * TILE_PX, top - y0 * TILE_PX
    image = canvas.crop(
        (int(offset_x), int(offset_y), int(offset_x) + WIDTH, int(offset_y) + HEIGHT)
    )

    draw = ImageDraw.Draw(image, "RGBA")
    mid_x, mid_y = WIDTH // 2, HEIGHT // 2
    # A ringed dot rather than a teardrop: a teardrop needs a font or a mask, and this is
    # unambiguous about WHICH pixel it means at any size.
    draw.ellipse((mid_x - 26, mid_y - 26, mid_x + 26, mid_y + 26), fill=(0, 85, 162, 46))
    draw.ellipse((mid_x - 15, mid_y - 15, mid_x + 15, mid_y + 15), fill=(255, 255, 255, 255))
    draw.ellipse((mid_x - 10, mid_y - 10, mid_x + 10, mid_y + 10), fill=(0, 85, 162, 255))

    # Attribution drawn into the image, so it travels with the picture even if the file is
    # ever viewed on its own. The card repeats it in HTML for anything reading the page.
    box = draw.textbbox((0, 0), ATTRIBUTION)
    pad, text_w, text_h = 8, box[2] - box[0], box[3] - box[1]
    draw.rectangle(
        (WIDTH - text_w - pad * 2, HEIGHT - text_h - pad * 2, WIDTH, HEIGHT),
        fill=(255, 255, 255, 205),
    )
    draw.text(
        (WIDTH - text_w - pad, HEIGHT - text_h - pad), ATTRIBUTION, fill=(60, 70, 84, 255)
    )
    return image


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for key, building in CAMPUS_BUILDINGS.items():
        image = render(building.lat, building.lon)
        path = OUT_DIR / f"{key}.webp"
        image.save(path, "WEBP", quality=82, method=6)
        print(
            f"{key:30s} {building.lat:.6f},{building.lon:.6f}  "
            f"{path.stat().st_size / 1024:6.1f} KB  {building.name}"
        )
    print(f"\n{len(CAMPUS_BUILDINGS)} buildings -> {OUT_DIR.relative_to(REPO)}")


if __name__ == "__main__":
    main()
