"""Render proof-of-delivery documents for the synthetic corpus.

The corpus cases carry a `document_uri` pointing at `fixtures/pod/NNNN.pdf`.
Until now those were strings with no files behind them, which meant the
extraction stage had nothing to extract from. This renders the documents.

Three quality tiers, because that is the actual problem in Indian ecommerce -
courier PODs arrive as clean carrier printouts, as flatbed scans, and as
photographs of a crumpled sheet taken on a phone in bad light. An extraction
number quoted without saying which tier it came from is close to meaningless,
so the manifest records the tier per document and the eval reports accuracy
broken down by it.

Ground truth is not invented here. Every rendered value comes from the case
JSON that already exists, so extraction can be scored against the same source
the oracle used - no second version of the truth to drift out of sync.

Usage:  python data/generator/fixtures.py --seed 20260824
"""

from __future__ import annotations

import argparse
import io
import json
import random
import shutil
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
OUT_DIR = DATA / "fixtures" / "pod"

#: Rendered at 150 dpi for crisp glyph shapes, then downsampled to OUTPUT_PAGE
#: before saving. Drawing large and shrinking gives realistic edge softening
#: for free, and a scan is never sharper than the page it came from.
PAGE = (1240, 1754)  # A4 at 150 dpi
MARGIN = 90

#: A4 at 100 dpi, which is 0.97 megapixels. Two reasons for that number: a
#: 60 MB fixture set has no business in a git repository, and Claude downsamples
#: images above roughly 1.15 MP anyway - so anything larger costs tokens and
#: buys nothing. Real courier scans are rarely better than this either.
OUTPUT_PAGE = (827, 1169)

#: How the mix of document quality is distributed. Weighted toward scans
#: because that is what merchants actually forward.
QUALITY_MIX = [("clean", 0.30), ("scanned", 0.45), ("photo", 0.25)]

FONT_CANDIDATES = [
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/calibri.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
]


def load_font(size: int) -> ImageFont.ImageFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    # Pillow's built-in bitmap face. Ugly, but it renders on any CI box, and a
    # fixture that only builds on one developer's laptop is not a fixture.
    return ImageFont.load_default(size=size)


def weighted(rng: random.Random, pairs: list[tuple[str, float]]) -> str:
    r, acc = rng.random(), 0.0
    for value, weight in pairs:
        acc += weight
        if r <= acc:
            return value
    return pairs[-1][0]


def _signature(draw: ImageDraw.ImageDraw, rng: random.Random, x: int, y: int) -> None:
    """A handwriting-ish squiggle. Not meant to be read - meant to be the thing
    a model has to decide whether it can read."""
    points = [(x, y)]
    for i in range(1, 26):
        points.append(
            (
                x + i * rng.randint(7, 13),
                y + rng.randint(-22, 22) - (12 if i % 4 == 0 else 0),
            )
        )
    draw.line(points, fill=(20, 20, 90), width=rng.randint(2, 4), joint="curve")


def render_pod(case: dict, tier: str, rng: random.Random) -> Image.Image:
    delivery = case["delivery"]
    order = case["order"]

    image = Image.new("RGB", PAGE, (255, 255, 255))
    draw = ImageDraw.Draw(image)

    title = load_font(52)
    label = load_font(30)
    value = load_font(34)
    small = load_font(24)

    y = MARGIN
    draw.text((MARGIN, y), delivery["carrier"].upper(), font=title, fill=(10, 10, 10))
    draw.text(
        (PAGE[0] - MARGIN - 380, y + 14),
        "PROOF OF DELIVERY",
        font=label,
        fill=(60, 60, 60),
    )
    y += 78
    draw.line([(MARGIN, y), (PAGE[0] - MARGIN, y)], fill=(0, 0, 0), width=3)
    y += 50

    # Barcode-ish block. Carries no data; it is visual clutter the extractor
    # has to ignore, which is part of the test.
    bar_x = MARGIN
    for _ in range(58):
        width = rng.choice([3, 3, 5, 8])
        if rng.random() < 0.62:
            draw.rectangle([bar_x, y, bar_x + width, y + 90], fill=(0, 0, 0))
        bar_x += width + rng.choice([3, 4, 6])
    y += 116
    draw.text((MARGIN, y), delivery["tracking_id"], font=value, fill=(0, 0, 0))
    y += 76

    delivered_at = (delivery.get("delivered_at") or "")[:10]
    rows = [
        ("Tracking ID", delivery["tracking_id"]),
        ("Order reference", order["id"]),
        ("Delivery date", delivered_at),
        ("Delivered to", delivery.get("delivered_to_address") or ""),
        ("Consignee", order["customer_email"].split("@")[0]),
        ("Status", "DELIVERED"),
    ]
    for name, text in rows:
        draw.text((MARGIN, y), name, font=label, fill=(90, 90, 90))
        wrapped = text if len(text) <= 46 else text[:46] + "-"
        draw.text((MARGIN + 340, y - 4), wrapped, font=value, fill=(0, 0, 0))
        if len(text) > 46:
            y += 42
            draw.text((MARGIN + 340, y - 4), text[46:], font=value, fill=(0, 0, 0))
        y += 62

    y += 40
    draw.line([(MARGIN, y), (PAGE[0] - MARGIN, y)], fill=(150, 150, 150), width=2)
    y += 44

    signed_by = delivery.get("signed_by")
    draw.text((MARGIN, y), "Received by", font=label, fill=(90, 90, 90))
    if signed_by:
        _signature(draw, rng, MARGIN + 350, y + 46)
        draw.text((MARGIN + 350, y + 88), signed_by, font=value, fill=(0, 0, 0))
    else:
        draw.text(
            (MARGIN + 350, y + 40),
            "LEFT AT DOOR - NO SIGNATURE",
            font=value,
            fill=(0, 0, 0),
        )
    y += 190

    draw.text(
        (MARGIN, PAGE[1] - MARGIN - 40),
        f"System generated. {delivery['carrier']} does not accept liability for transcription errors.",
        font=small,
        fill=(120, 120, 120),
    )

    # A rotated DELIVERED stamp, which is exactly the kind of overlapping ink
    # that trips naive text extraction.
    stamp = Image.new("RGBA", (560, 190), (0, 0, 0, 0))
    stamp_draw = ImageDraw.Draw(stamp)
    stamp_draw.rectangle([4, 4, 556, 186], outline=(200, 40, 40, 210), width=7)
    stamp_draw.text((44, 52), "DELIVERED", font=load_font(76), fill=(200, 40, 40, 210))
    stamp = stamp.rotate(rng.randint(-18, -6), expand=True, resample=Image.BICUBIC)
    image.paste(stamp, (PAGE[0] - 700, PAGE[1] - 620), stamp)

    return degrade(image, tier, rng)


def degrade(image: Image.Image, tier: str, rng: random.Random) -> Image.Image:
    """Make the page look like it actually reached the merchant.

    The first version of this was far too gentle - the `photo` tier came out
    almost pristine, which would have made extraction accuracy identical across
    all three tiers and the whole breakdown meaningless. A hard tier has to be
    genuinely hard or it is measuring nothing.
    """
    if tier == "clean":
        return image

    if tier == "scanned":
        image = image.rotate(
            rng.uniform(-2.4, 2.4), expand=False, fillcolor=(255, 255, 255),
            resample=Image.BICUBIC,
        )
        image = image.filter(ImageFilter.GaussianBlur(rng.uniform(0.5, 1.1)))
        image = ImageEnhance.Contrast(image).enhance(rng.uniform(0.72, 0.88))
        return add_noise(image, rng, amount=14)

    # photo: handheld, off-axis, one lamp, slight motion, then compressed by
    # whatever messaging app the merchant forwarded it through.
    image = perspective(image, rng)
    image = image.filter(ImageFilter.GaussianBlur(rng.uniform(1.4, 2.6)))
    image = uneven_light(image, rng)
    image = ImageEnhance.Contrast(image).enhance(rng.uniform(0.50, 0.68))
    image = ImageEnhance.Brightness(image).enhance(rng.uniform(0.86, 1.06))
    image = add_noise(image, rng, amount=26)
    return jpeg_artifacts(image, rng, quality=rng.randint(22, 38))


def perspective(image: Image.Image, rng: random.Random) -> Image.Image:
    """Shoot the page from an angle, the way a phone actually captures paper."""
    width, height = image.size
    shift = lambda scale: rng.uniform(-scale, scale)  # noqa: E731
    dx, dy = width * 0.055, height * 0.045
    # Destination quad: each corner pulled independently.
    quad = (
        shift(dx), shift(dy),
        shift(dx), height - shift(dy),
        width - shift(dx), height - shift(dy),
        width - shift(dx), shift(dy),
    )
    return image.transform(
        image.size, Image.QUAD, quad, resample=Image.BICUBIC, fillcolor=(246, 245, 242)
    )


def uneven_light(image: Image.Image, rng: random.Random) -> Image.Image:
    """One lamp, off to a side. Multiplied rather than composited toward white -
    compositing washes the page out uniformly, which is not what a bad photo
    looks like; real ones have a bright lobe and dim corners."""
    width, height = image.size
    gradient = Image.new("L", (width, height), 96)
    draw = ImageDraw.Draw(gradient)
    cx, cy = rng.randint(0, width), rng.randint(0, height)
    radius = rng.randint(int(width * 0.35), int(width * 0.75))
    draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=255)
    gradient = gradient.filter(ImageFilter.GaussianBlur(180))
    return ImageChops.multiply(image, Image.merge("RGB", (gradient, gradient, gradient)))


def jpeg_artifacts(image: Image.Image, rng: random.Random, quality: int) -> Image.Image:
    """Round-trip through heavy JPEG. Blocking artefacts around glyph edges are
    a large part of why phone-forwarded documents are hard to read."""
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=quality)
    buffer.seek(0)
    return Image.open(buffer).convert("RGB")


def add_noise(image: Image.Image, rng: random.Random, amount: int) -> Image.Image:
    pixels = image.load()
    width, height = image.size
    # Sparse salt-and-pepper. Enough to be visible, cheap enough for 300 pages.
    for _ in range((width * height) // (900 // max(amount, 1))):
        x, y = rng.randrange(width), rng.randrange(height)
        shade = rng.randint(0, 90) if rng.random() < 0.5 else rng.randint(170, 255)
        pixels[x, y] = (shade, shade, shade)
    return image


def ground_truth(case: dict, tier: str, path: Path) -> dict:
    """What a perfect extractor should return. Taken from the case JSON, never
    re-authored, so there is exactly one version of the truth."""
    delivery = case["delivery"]
    return {
        "case_id": case["case_id"],
        "document": str(path.relative_to(DATA)).replace("\\", "/"),
        "quality": tier,
        "expected": {
            "tracking_id": delivery["tracking_id"],
            "carrier": delivery["carrier"],
            "delivered_at": (delivery.get("delivered_at") or "")[:10],
            "signed_by": delivery.get("signed_by"),
            "delivered_to_address": delivery.get("delivered_to_address"),
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=20260824)
    ap.add_argument("--limit", type=int, default=0, help="render only the first N (for a quick look)")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    case_paths = sorted(
        list((DATA / "train").glob("case_*.json")) + list((DATA / "test").glob("case_*.json"))
    )

    entries: list[dict] = []
    for case_path in case_paths:
        case = json.loads(case_path.read_text(encoding="utf-8"))
        if not case.get("delivery"):
            continue  # subscriptions and undelivered orders have no POD to render
        if args.limit and len(entries) >= args.limit:
            break

        tier = weighted(rng, QUALITY_MIX)
        # document_uri is "fixtures/pod/NNNN.pdf" relative to data/
        out_path = DATA / case["delivery"]["document_uri"]
        out_path.parent.mkdir(parents=True, exist_ok=True)

        page = render_pod(case, tier, rng)
        page = page.resize(OUTPUT_PAGE, Image.LANCZOS)
        if tier != "clean":
            # Scans and phone photos of a courier slip are rarely colour, and
            # greyscale is a third of the bytes for no loss of legibility.
            page = page.convert("L")
        page.save(out_path, "PDF", resolution=100.0)
        entries.append(ground_truth(case, tier, out_path))

    by_tier: dict[str, int] = {}
    for entry in entries:
        by_tier[entry["quality"]] = by_tier.get(entry["quality"], 0) + 1

    manifest = {
        "seed": args.seed,
        "documents": len(entries),
        "by_quality": dict(sorted(by_tier.items())),
        "signed_fraction": round(
            sum(1 for e in entries if e["expected"]["signed_by"]) / len(entries), 4
        )
        if entries
        else 0.0,
        "generated_by": "data/generator/fixtures.py",
        "note": "Synthetic delivery documents. No real courier or customer records.",
        "entries": entries,
    }
    (DATA / "fixtures" / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )

    total_kb = sum(p.stat().st_size for p in OUT_DIR.glob("*.pdf")) // 1024
    print(f"rendered {len(entries)} PODs  ({total_kb} KB)")
    print(f"quality mix   {manifest['by_quality']}")
    print(f"signed        {manifest['signed_fraction']:.0%}")


if __name__ == "__main__":
    main()
