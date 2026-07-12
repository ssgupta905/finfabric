"""Renders a synthetic card and emits, for each sample:

  images/<id>.jpg     the degraded photo of the card
  labels/<id>.txt     YOLO detector labels (normalised cx cy w h, one per field)
  truth/<id>.json     ground truth field values + MRZ, for CER and gate metrics

Font families are split into TRAIN_FONTS and HOLDOUT_FONTS. The holdout split
renders with fonts and background tints the model has never seen, so the eval
measures generalisation rather than template memorisation.
"""

import argparse, json, os, random
from PIL import Image, ImageDraw, ImageFont

from schema import (CARD_W, CARD_H, FIELDS, MRZ_BOX, MRZ_CLASS, CLASS_NAMES_WITH_MRZ)
from records import make_record
from degrade import degrade

F = "/usr/share/fonts/truetype"
TRAIN_FONTS = [
    (f"{F}/dejavu/DejaVuSans.ttf", f"{F}/dejavu/DejaVuSans-Bold.ttf"),
    (f"{F}/liberation/LiberationSans-Regular.ttf", f"{F}/liberation/LiberationSans-Bold.ttf"),
]
HOLDOUT_FONTS = [
    (f"{F}/freefont/FreeSans.ttf", f"{F}/freefont/FreeSansBold.ttf"),
]
MONO = f"{F}/dejavu/DejaVuSansMono.ttf"

TRAIN_TINTS = [(246, 245, 238), (240, 244, 246), (247, 242, 240)]
HOLDOUT_TINTS = [(238, 246, 240), (245, 240, 247)]


def _guilloche(draw, rng, tint):
    """Cheap security-pattern texture. Gives the detector something to ignore."""
    ink = tuple(max(0, c - 26) for c in tint)
    for i in range(0, CARD_W, 7):
        off = int(14 * (1 + rng.random()) * ((i % 53) / 53 - 0.5))
        draw.line([(i, 0), (i + off, CARD_H)], fill=ink, width=1)


def render(rec, rng, fonts, tint):
    reg, bold = fonts
    img = Image.new("RGB", (CARD_W, CARD_H), tint)
    d = ImageDraw.Draw(img)
    _guilloche(d, rng, tint)

    f_label = ImageFont.truetype(reg, 17)
    f_value = ImageFont.truetype(bold, 27)
    f_head = ImageFont.truetype(bold, 30)
    f_mono = ImageFont.truetype(MONO, 24)

    d.rectangle([0, 0, CARD_W - 1, CARD_H - 1], outline=(60, 60, 70), width=3)
    d.text((40, 34), "NATIONAL RESIDENCE CARD", font=f_head, fill=(30, 40, 90))

    # Portrait placeholder — deliberately abstract, never a real or generated face.
    d.rectangle([40, 96, 260, 380], fill=(214, 216, 220), outline=(120, 122, 130), width=2)
    d.ellipse([110, 150, 190, 230], fill=(178, 181, 188))
    d.ellipse([90, 250, 210, 380], fill=(178, 181, 188))

    boxes = []
    for fld in FIELDS:
        x, y, w, h = fld.box
        d.text((x, y - 20), fld.label, font=f_label, fill=(96, 100, 112))
        val = rec[fld.name]
        if fld.multiline:
            for i, line in enumerate(val.split("\n")):
                d.text((x + 2, y + 2 + i * 40), line, font=f_value, fill=(18, 20, 30))
        else:
            d.text((x + 2, y + 2), val, font=f_value, fill=(18, 20, 30))
        boxes.append((fld.cls, x - 4, y - 4, w + 8, h + 8))

    mx, my, mw, mh = MRZ_BOX
    d.rectangle([mx - 6, my - 6, mx + mw + 6, my + mh + 6], fill=(252, 252, 250))
    for i, line in enumerate(rec["_mrz"]):
        d.text((mx, my + i * 26), line, font=f_mono, fill=(20, 20, 24))
    boxes.append((MRZ_CLASS, mx - 6, my - 6, mw + 12, mh + 12))
    return img, boxes


def yolo_lines(boxes, w, h):
    out = []
    for cls, x, y, bw, bh in boxes:
        out.append(f"{cls} {(x + bw / 2) / w:.6f} {(y + bh / 2) / h:.6f} {bw / w:.6f} {bh / h:.6f}")
    return "\n".join(out)


def build(out_dir, n, seed, holdout):
    rng = random.Random(seed)
    fonts_pool = HOLDOUT_FONTS if holdout else TRAIN_FONTS
    tints = HOLDOUT_TINTS if holdout else TRAIN_TINTS
    for sub in ("images", "labels", "truth"):
        os.makedirs(f"{out_dir}/{sub}", exist_ok=True)

    for i in range(n):
        rec = make_record(rng)
        img, boxes = render(rec, rng, rng.choice(fonts_pool), rng.choice(tints))
        img, boxes = degrade(img, boxes, rng)

        sid = f"{i:06d}"
        img.save(f"{out_dir}/images/{sid}.jpg", quality=rng.randint(62, 92))
        with open(f"{out_dir}/labels/{sid}.txt", "w") as f:
            f.write(yolo_lines(boxes, img.width, img.height))
        truth = {k: v for k, v in rec.items() if not k.startswith("_")}
        truth["mrz"] = rec["_mrz"]
        with open(f"{out_dir}/truth/{sid}.json", "w") as f:
            json.dump(truth, f, indent=1)

    with open(f"{out_dir}/classes.txt", "w") as f:
        f.write("\n".join(CLASS_NAMES_WITH_MRZ))
    return n


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    p.add_argument("--n", type=int, default=1000)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--holdout", action="store_true",
                   help="render with unseen fonts and tints — use for the eval split only")
    a = p.parse_args()
    print(f"wrote {build(a.out, a.n, a.seed, a.holdout)} samples to {a.out}")
