"""Make lining figures the default digits in the Cormorant Garamond fonts.

Cormorant uses old-style figures by default; for "45 LAT" and the dates we want
the calmer lining figures (OpenType 'lnum'). This bakes the substitution into
the cmap so any renderer gets them. Writes *-Lining.ttf next to the originals.

Usage: python3 scripts/make_lining_fonts.py
"""
import glob
import os

from fontTools.ttLib import TTFont

FONTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts")


def lnum_map(font):
    gsub = font["GSUB"].table
    mapping = {}
    for rec in gsub.FeatureList.FeatureRecord:
        if rec.FeatureTag != "lnum":
            continue
        for idx in rec.Feature.LookupListIndex:
            for sub in gsub.LookupList.Lookup[idx].SubTable:
                if hasattr(sub, "ExtSubTable"):
                    sub = sub.ExtSubTable
                mapping.update(getattr(sub, "mapping", {}) or {})
    return mapping


for path in sorted(glob.glob(os.path.join(FONTS, "CormorantGaramond-*.ttf"))):
    if path.endswith("-Lining.ttf"):
        continue
    font = TTFont(path)
    sub = lnum_map(font)
    changed = 0
    for table in font["cmap"].tables:
        for code in range(ord("0"), ord("9") + 1):
            glyph = table.cmap.get(code)
            if glyph in sub:
                table.cmap[code] = sub[glyph]
                changed += 1
    out = path[:-4] + "-Lining.ttf"
    font.save(out)
    print(os.path.basename(out), "digits remapped:", changed)
