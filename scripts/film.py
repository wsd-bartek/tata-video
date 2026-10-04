"""Film engine for the long version: photos + plates, camera moves, FX, titles, 35mm look.

The edit itself (shots, titles, voice-over placement, music plan) lives in a config
module, e.g. regie/private/film_config.py, so personal material never has to be in git.

Usage:
  python3 scripts/film.py regie/private/film_config.py --stills 10 52.3 ...
  python3 scripts/film.py regie/private/film_config.py --range 0 30 --out build/part.mp4
  python3 scripts/film.py regie/private/film_config.py --audio build/long_mix.wav --out build/film_long.mp4
"""
import argparse
import importlib.util
import math
import multiprocessing as mp
import os
import subprocess
import sys

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps

sys.path.insert(0, os.path.dirname(__file__))
from render import GRADES as BASE_GRADES, Look, grade_lut, scale_img, smooth  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W, H, FPS = 1920, 1080, 24
LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)
CFG = None
STATE = {}


def load_config(path):
    spec = importlib.util.spec_from_file_location("film_config", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def path_of(p):
    return p if os.path.isabs(p) else os.path.join(ROOT, p)


def ease(u):
    """Mostly linear camera move with soft ends."""
    u = min(1.0, max(0.0, u))
    return 0.6 * u + 0.4 * (0.5 - 0.5 * math.cos(math.pi * u))


def auto_levels(im, strength=0.6):
    """Gentle per-channel levels so phone, camera and scanned photos sit together."""
    a = np.asarray(im, np.float32)
    lo = np.percentile(a, 0.6, axis=(0, 1))
    hi = np.percentile(a, 99.4, axis=(0, 1))
    stretched = (a - lo) / np.maximum(hi - lo, 1) * 255
    out = a * (1 - strength) + stretched * strength
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))


def light_image(spec):
    lx, ly, radius, color, strength = spec
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    d = np.sqrt((xx / W - lx) ** 2 + ((yy - ly * H) / W) ** 2) / radius
    k = np.clip(1 - d, 0, 1) ** 1.6 * strength
    return Image.fromarray(np.clip(k[..., None] * np.array(color, np.float32), 0, 255).astype(np.uint8))


# --------------------------------------------------------------------------
# shots
# --------------------------------------------------------------------------
class Shot:
    def __init__(self, spec, start, end, d_in, d_out, grades):
        self.spec = spec
        self.start, self.end, self.d_in, self.d_out = start, end, d_in, d_out
        self.vis_start, self.vis_end = start - d_in / 2, end + d_out / 2
        im = ImageOps.exif_transpose(Image.open(path_of(spec["src"]))).convert("RGB")
        if spec.get("crop"):                       # e.g. a photographed print
            x0, y0, x1, y1 = spec["crop"]
            im = im.crop((round(x0 * im.width), round(y0 * im.height), round(x1 * im.width), round(y1 * im.height)))
        if spec.get("normalize"):
            im = auto_levels(im, spec.get("normalize") if isinstance(spec.get("normalize"), float) else 0.6)
        self.box0, self.box1 = spec["box0"], spec.get("box1", spec["box0"])
        wmin = min(self.box0[2], self.box1[2])
        wmax = max(self.box0[2], self.box1[2])
        # keep the largest crop at <= ~1.25x output size (no aliasing), never upscale the source
        s = min(1.0, (W * 1.25) / (wmax * im.width))
        if s < 0.999:
            im = im.resize((round(im.width * s), round(im.height * s)), Image.LANCZOS)
        self.img = im
        self.upscale = W / (wmin * im.width)
        self.lut = grade_lut(grades[spec["grade"]])
        self.light = light_image(spec["light"]) if spec.get("light") else None
        self.fx = set(spec.get("fx", []))

    def alpha(self, t):
        a = 1.0
        if self.d_in > 0 and t < self.start + self.d_in / 2:
            a = smooth((t - self.vis_start) / self.d_in)
        if self.d_out > 0 and t > self.end - self.d_out / 2:
            a = min(a, 1 - smooth((t - (self.end - self.d_out / 2)) / self.d_out))
        return a

    def box_at(self, t):
        u = ease((t - self.vis_start) / max(1e-6, self.vis_end - self.vis_start))
        cx, cy, w = (a + (b - a) * u for a, b in zip(self.box0, self.box1))
        iw, ih = self.img.size
        bw = w * iw
        bh = bw * H / W
        if bh > ih:                                # box taller than the image: fit height
            bh = ih
            bw = bh * W / H
        hx, hy = bw / 2 / iw, bh / 2 / ih
        cx = min(max(cx, hx), 1 - hx)
        cy = min(max(cy, hy), 1 - hy)
        return cx * iw, cy * ih, bw

    def render(self, t, weave):
        cx, cy, bw = self.box_at(t)
        scale = W / bw
        inv = 1 / scale
        cx += weave[0] * inv
        cy += weave[1] * inv
        out = self.img.transform((W, H), Image.AFFINE, (inv, 0, cx - W / 2 * inv, 0, inv, cy - H / 2 * inv),
                                 resample=Image.BICUBIC if scale > 1.05 else Image.BILINEAR)
        out = out.filter(self.lut)
        if self.light is not None:
            out = ImageChops.screen(out, self.light)
        return out


# --------------------------------------------------------------------------
# effects
# --------------------------------------------------------------------------
def noise_texture(w, h, seed, octaves=((6, 1.0), (12, 0.55), (24, 0.3), (48, 0.15))):
    rng = np.random.default_rng(seed)
    acc = np.zeros((h, w), np.float32)
    for cells, amp in octaves:
        n = rng.random((max(2, cells * h // w), cells)).astype(np.float32)
        im = Image.fromarray((n * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC)
        acc += amp * np.asarray(im, np.float32) / 255
    acc -= acc.min()
    return acc / acc.max()


class Fog:
    def __init__(self):
        self.t1 = noise_texture(W * 2, H, 5)
        self.t2 = noise_texture(W * 2, H, 9, octaves=((4, 1.0), (9, 0.5), (20, 0.25)))
        yy = np.linspace(0, 1, H, dtype=np.float32)[:, None]
        self.band = np.exp(-((yy - 0.62) / 0.28) ** 2)          # fog hugs the ground
        self.color = Image.new("RGB", (W, H), (196, 202, 210))

    def apply(self, img, t, strength):
        x1 = int(t * 26) % W
        x2 = int(W - (t * 14) % W)
        layer = 0.6 * self.t1[:, x1:x1 + W] + 0.4 * self.t2[:, x2:x2 + W]
        alpha = np.clip((layer - 0.35) * 1.6, 0, 1) * self.band * strength
        mask = Image.fromarray((alpha * 255).astype(np.uint8))
        return Image.composite(self.color, img, mask)


class Leaves:
    PALETTE = [(214, 140, 40), (190, 104, 32), (232, 172, 64), (172, 82, 26), (205, 150, 52)]

    def __init__(self, n=30, seed=4):
        rng = np.random.default_rng(seed)
        self.p = [dict(x=rng.uniform(-0.1, 1.1) * W, y=rng.uniform(0, H + 120), v=rng.uniform(55, 130),
                       amp=rng.uniform(20, 70), f=rng.uniform(0.25, 0.7), ph=rng.uniform(0, 6.3),
                       rot=rng.uniform(0, 6.3), vr=rng.uniform(-2.2, 2.2), size=rng.uniform(9, 26),
                       col=self.PALETTE[int(rng.integers(0, len(self.PALETTE)))]) for _ in range(n)]

    def apply(self, img, t, strength):
        near = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        far = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        dn, dfar = ImageDraw.Draw(near), ImageDraw.Draw(far)
        for p in self.p:
            y = (p["y"] + p["v"] * t) % (H + 120) - 60
            x = p["x"] + p["amp"] * math.sin(2 * math.pi * p["f"] * t + p["ph"]) + 18 * t
            x = (x + 100) % (W + 200) - 100
            r = p["rot"] + p["vr"] * t
            s = p["size"]
            flip = abs(math.cos(r * 0.7)) * 0.75 + 0.25            # tumbling
            pts = []
            for k in range(10):
                a = 2 * math.pi * k / 10
                lx, ly = s * math.cos(a), s * 0.45 * flip * math.sin(a)
                pts.append((x + lx * math.cos(r) - ly * math.sin(r), y + lx * math.sin(r) + ly * math.cos(r)))
            (dn if s > 18 else dfar).polygon(pts, fill=p["col"] + (int(220 * strength),))
        layer = Image.alpha_composite(far.filter(ImageFilter.GaussianBlur(1.6)), near.filter(ImageFilter.GaussianBlur(3.2)))
        out = img.convert("RGBA")
        out.alpha_composite(layer)
        return out.convert("RGB")


class Leak:
    """Warm light leak sweeping across the frame (used on chapter changes)."""

    def __init__(self):
        yy, xx = np.mgrid[0:H // 4, 0:W // 2].astype(np.float32)
        cx, cy = W // 4, H // 8
        d = np.sqrt(((xx - cx) / (W // 4)) ** 2 + ((yy - cy) / (H // 3)) ** 2)
        k = np.clip(1 - d, 0, 1) ** 1.8
        rgb = k[..., None] * np.array([255, 150, 70], np.float32)
        core = np.clip(1 - d * 2.2, 0, 1)[..., None] * np.array([90, 80, 60], np.float32)
        self.blob = Image.fromarray(np.clip(rgb + core, 0, 255).astype(np.uint8)).resize((W, H), Image.BICUBIC)

    def apply(self, img, t, t0, dur):
        u = (t - t0) / dur
        if not 0 <= u <= 1:
            return img
        k = math.sin(math.pi * u) ** 1.5 * 0.85
        shift = int((u - 0.5) * W * 1.1)
        layer = ImageChops.offset(self.blob, shift, 0)
        return ImageChops.screen(img, scale_img(layer, k))


def dust(img, frame_no, strength):
    rng = np.random.default_rng(7000 + frame_no)
    d = ImageDraw.Draw(img)
    for _ in range(int(rng.integers(0, 4) * strength + 0.5)):
        x, y, r = rng.uniform(0, W), rng.uniform(0, H), rng.uniform(0.8, 2.6)
        c = (18, 16, 14) if rng.random() < 0.7 else (236, 230, 220)
        d.ellipse((x - r, y - r, x + r, y + r), fill=c)
    if rng.random() < 0.05 * strength:                     # rare vertical scratch
        x = rng.uniform(0.1, 0.9) * W
        d.line((x, 0, x + rng.uniform(-6, 6), H), fill=(214, 208, 198), width=1)
    return img


# --------------------------------------------------------------------------
# titles
# --------------------------------------------------------------------------
def text_mask(text, font_name, size, tracking, y_frac, x_frac=0.5):
    font = ImageFont.truetype(os.path.join(ROOT, "assets", "fonts", font_name), size)
    track = tracking * size
    widths = [font.getlength(c) for c in text]
    total = sum(widths) + track * (len(text) - 1)
    bbox = font.getbbox(text)
    x, y0 = W * x_frac - total / 2, H * y_frac - (bbox[1] + bbox[3]) / 2
    mask = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(mask)
    for c, w in zip(text, widths):
        d.text((x, y0), c, font=font, fill=255)
        x += w + track
    return mask


class Title:
    def __init__(self, spec):
        self.start, self.end = spec["start"], spec["end"]
        self.fade_in = spec.get("fade_in", spec.get("fade", 0.8))
        self.fade_out = spec.get("fade_out", spec.get("fade", 0.8))
        mask = text_mask(spec["text"], spec["font"], spec["size"], spec.get("tracking", 0.1), spec.get("y", 0.5),
                         spec.get("x", 0.5))
        b, pad = mask.getbbox(), 60
        self.box = (max(0, b[0] - pad), max(0, b[1] - pad), min(W, b[2] + pad), min(H, b[3] + pad))
        self.mask = mask.crop(self.box)
        self.shadow = self.mask.filter(ImageFilter.GaussianBlur(11))
        self.shadow_k = spec.get("shadow", 0.6)
        self.color = Image.new("RGB", self.mask.size, tuple(spec.get("color", (246, 237, 221))))

    def draw(self, img, t):
        if t < self.start or t > self.end:
            return img
        a = min(smooth((t - self.start) / self.fade_in), smooth((self.end - t) / self.fade_out))
        if a <= 0:
            return img
        blur = 5.0 * (1 - smooth((t - self.start) / self.fade_in))
        m = self.mask.filter(ImageFilter.GaussianBlur(blur)) if blur > 0.3 else self.mask
        region = img.crop(self.box)
        if self.shadow_k > 0:
            shade = self.shadow.point([int(255 - min(1.0, self.shadow_k * a * v / 160) * 255) for v in range(256)])
            region = ImageChops.multiply(region, shade.convert("RGB"))
        region = Image.composite(self.color, region, m.point([int(v * a) for v in range(256)]))
        img.paste(region, self.box[:2])
        return img


# --------------------------------------------------------------------------
# assembly
# --------------------------------------------------------------------------
def build_shots(cfg):
    grades = dict(BASE_GRADES)
    grades.update(getattr(cfg, "GRADES", {}))
    specs = cfg.SHOTS
    shots = []
    for i, s in enumerate(specs):
        nxt = specs[i + 1] if i + 1 < len(specs) else None
        end = nxt["at"] if nxt else cfg.END
        d_in = s.get("d", 0.0)
        d_out = nxt.get("d", 0.0) if nxt else 0.0
        shots.append(Shot(s, s["at"], end, d_in, d_out, grades))
    return shots


def init_state(cfg_path):
    global CFG
    CFG = load_config(cfg_path)
    shots = build_shots(CFG)
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    centre = np.exp(-(((xx - W / 2) / (W * 0.38)) ** 2 + ((yy - H / 2) / (H * 0.30)) ** 2))
    STATE.update(shots=shots, look=Look(), fog=Fog(), leaves=Leaves(), leak=Leak(),
                 titles=[Title(t) for t in CFG.TITLES],
                 centre=Image.fromarray((centre * 255).astype(np.uint8)))
    for s in shots:
        warn = "  (upscale %.2fx)" % s.upscale if s.upscale > 1.35 else ""
        print(f"  {s.vis_start:7.2f}-{s.vis_end:7.2f}s  {os.path.basename(s.spec['src'])[:34]:34s} {s.spec['grade']}{warn}",
              file=sys.stderr)


def weave_at(t):
    return (0.55 * math.sin(2 * math.pi * 0.61 * t + 0.3) + 0.25 * math.sin(2 * math.pi * 1.73 * t + 1.1),
            0.45 * math.sin(2 * math.pi * 0.47 * t + 2.0) + 0.20 * math.sin(2 * math.pi * 2.11 * t + 0.7))


def global_gain(t):
    k = 1.0
    for t0, t1, t2, t3 in CFG.FADES:          # trapezoid dips: out t0->t1, black, in t2->t3
        if t0 <= t <= t3:
            if t < t1:
                k = min(k, 1 - smooth((t - t0) / max(1e-6, t1 - t0)))
            elif t <= t2:
                k = 0.0
            else:
                k = min(k, smooth((t - t2) / max(1e-6, t3 - t2)))
    return k


def dim_at(t):
    a = 0.0
    for t0, t1, amount in getattr(CFG, "DIMS", []):
        if t0 - 0.4 <= t <= t1 + 0.4:
            a = max(a, amount * min(smooth((t - t0 + 0.4) / 0.8), smooth((t1 + 0.4 - t) / 0.8)))
    return a


def render_frame(frame_no):
    t = frame_no / FPS
    weave = weave_at(t)
    img = None
    for s in STATE["shots"]:
        if not (s.vis_start <= t <= s.vis_end):
            continue
        a = s.alpha(t)
        if a <= 0:
            continue
        pic = s.render(t, weave)
        if "fog" in s.fx:
            pic = STATE["fog"].apply(pic, t, s.spec.get("fog", 0.55))
        if "leaves" in s.fx:
            pic = STATE["leaves"].apply(pic, t, 1.0)
        img = pic if img is None else Image.blend(img, pic, a)
        fx = s.fx
    if img is None:
        img = Image.new("RGB", (W, H), (0, 0, 0))
        fx = set()
    for t0, dur in getattr(CFG, "LEAKS", []):
        img = STATE["leak"].apply(img, t, t0, dur)
    k = global_gain(t) * (1 + 0.006 * np.random.default_rng(1000 + frame_no).standard_normal())
    img = scale_img(img, k)
    dim = dim_at(t)
    if dim > 0:
        shade = STATE["centre"].point([int(255 - dim * v) for v in range(256)]).convert("RGB")
        img = ImageChops.multiply(img, shade)
    if "dust" in fx and k > 0.2:
        img = dust(img, frame_no, 1.0)
    for title in STATE["titles"]:
        img = title.draw(img, t)
    return STATE["look"].finish(img, frame_no)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--out", default=os.path.join(ROOT, "build", "film_long.mp4"))
    ap.add_argument("--audio")
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--stills", type=float, nargs="*")
    ap.add_argument("--still-width", type=int, default=960)
    ap.add_argument("--range", type=float, nargs=2)
    ap.add_argument("--preset", default="slow")
    ap.add_argument("--crf", default="18")
    args = ap.parse_args()
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    init_state(args.config)
    if args.stills:
        for t in args.stills:
            img = Image.fromarray(render_frame(int(round(t * FPS))))
            if args.still_width != W:
                img = img.resize((args.still_width, round(args.still_width * H / W)), Image.LANCZOS)
            p = os.path.join(os.path.dirname(os.path.abspath(args.out)), f"f_{t:07.2f}.jpg")
            img.save(p, quality=84)
            print("wrote", p, file=sys.stderr)
        return
    t0, t1 = args.range if args.range else (0.0, CFG.END)
    first, last = int(round(t0 * FPS)), int(round(t1 * FPS))
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
           "-i", "-"]
    if args.audio:
        cmd += ["-ss", f"{first / FPS:.3f}", "-i", args.audio, "-map", "0:v", "-map", "1:a", "-c:a", "aac",
                "-b:a", "256k", "-shortest"]
    cmd += ["-c:v", "libx264", "-preset", args.preset, "-crf", args.crf, "-tune", "grain", "-pix_fmt", "yuv420p",
            "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-movflags", "+faststart",
            args.out]
    enc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    with mp.get_context("fork").Pool(args.workers) as pool:
        for i, frame in enumerate(pool.imap(render_frame, range(first, last), chunksize=4)):
            enc.stdin.write(frame.tobytes())
            if i % 240 == 0:
                print(f"frame {i}/{last - first}", file=sys.stderr, flush=True)
    enc.stdin.close()
    if enc.wait() != 0:
        raise SystemExit("ffmpeg failed")
    print("wrote", args.out, file=sys.stderr)


if __name__ == "__main__":
    main()
