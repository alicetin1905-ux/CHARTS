"""Render the GlassSphere app icon: a glass orb on a stand, with order-book bands (bids green, asks red)
refracted inside, on the page's dark background.

    python3 scripts/glasssphere_icon.py        writes icons/*.png
"""
import os

import numpy as np
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'icons')
S = 2048  # rendered at 2x, then scaled down

BG_TOP, BG_BOT = np.array([10, 24, 20]), np.array([4, 7, 6])     # the page's --bg family
UP, DN = np.array([53, 255, 176]), np.array([255, 77, 106])      # --up / --dn
GLASS = np.array([110, 200, 255])


def smooth(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


def render(pad=0.0):
    """pad shrinks the artwork toward the centre (for the maskable icon's safe zone)."""
    yy, xx = np.mgrid[0:S, 0:S].astype(np.float64) / S
    k = 1 - 2 * pad
    xx, yy = (xx - 0.5) / k + 0.5, (yy - 0.5) / k + 0.5
    img = BG_TOP * (1 - yy[..., None]) + BG_BOT * yy[..., None]

    cx, cy, R = 0.5, 0.445, 0.315
    dx, dy = (xx - cx) / R, (yy - cy) / R
    r2 = dx * dx + dy * dy
    # soft green glow behind the orb
    glow = np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * 0.22 ** 2)) * 0.10
    img = img + UP * glow[..., None]

    # stand: a tapered base with a lit top rim, under the orb
    by0, by1 = 0.735, 0.875
    t = np.clip((yy - by0) / (by1 - by0), 0, 1)
    half = 0.17 + 0.07 * t
    base = (yy >= by0) & (yy <= by1) & (np.abs(xx - cx) <= half)
    shade = 0.55 + 0.45 * np.clip(1 - np.abs(xx - cx) / np.maximum(half, 1e-6), 0, 1) ** 0.6
    metal = (np.array([38, 52, 48]) * (1 - t[..., None]) + np.array([16, 22, 20]) * t[..., None]) * shade[..., None]
    rim = smooth(0.012, 0.0, np.abs(yy - by0)) * (np.abs(xx - cx) <= half)
    metal = metal + np.array([90, 140, 125]) * rim[..., None] * 0.8
    img = np.where(base[..., None], metal, img)
    # contact shadow of the orb on the stand
    sh = np.exp(-(((xx - cx) / 0.16) ** 2 + ((yy - by0) / 0.018) ** 2)) * 0.6
    img = img * (1 - sh[..., None] * base[..., None])

    # the orb
    inside = r2 <= 1
    z = np.sqrt(np.clip(1 - r2, 0, 1))
    # refraction: inner content is seen magnified and bent toward the edge
    ry = dy * (0.55 + 0.45 * z)
    rx = dx * (0.55 + 0.45 * z)
    # order-book bands: asks (red) above the middle, bids (green) below, stronger near the middle
    band_y = ry * 9.0
    stripe = (0.5 + 0.5 * np.cos(band_y * np.pi)) ** 6
    width = np.exp(-(rx * 1.7) ** 2) * np.clip(1.15 - np.abs(ry) * 0.9, 0, 1)
    seed = np.floor(band_y + 0.5)
    lengths = 0.35 + 0.65 * np.abs(np.sin(seed * 12.9898) * 43758.5453 % 1)   # each band its own length
    bar = stripe * smooth(lengths * 0.9 + 0.05, lengths * 0.9 - 0.05, np.abs(rx)) * width
    col = np.where((ry < 0)[..., None], DN, UP)
    interior = np.array([6, 22, 24]) * (0.6 + 0.4 * z[..., None])
    interior = interior + col * (bar * 0.85)[..., None]
    # a lit horizontal line through the middle: the current price
    price = np.exp(-(ry / 0.018) ** 2) * np.exp(-(rx * 1.2) ** 2)
    interior = interior + np.array([235, 250, 245]) * price[..., None] * 0.9
    core = np.exp(-(rx ** 2 + ry ** 2) / (2 * 0.35 ** 2)) * 0.18
    interior = interior + UP * core[..., None]
    # glass: fresnel rim, tinted edge
    fres = (1 - z) ** 3
    orb = interior * (1 - fres[..., None] * 0.7) + GLASS * (fres * 0.85)[..., None]
    # specular highlights
    h1 = np.exp(-(((dx + 0.38) / 0.24) ** 2 + ((dy + 0.48) / 0.15) ** 2)) * 0.95
    h2 = np.exp(-(((dx - 0.42) / 0.12) ** 2 + ((dy - 0.55) / 0.07) ** 2)) * 0.35
    arc = smooth(0.06, 0.0, np.abs(np.sqrt(r2) - 0.9)) * smooth(-0.1, -0.6, dy) * smooth(0.2, -0.3, dx) * 0.45
    orb = orb + np.array([255, 255, 255]) * (h1 + h2 + arc)[..., None]
    edge = smooth(1.0, 0.985, np.sqrt(r2))                 # anti-aliased edge
    img = img * (1 - edge[..., None] * inside[..., None]) + orb * (edge * inside)[..., None]
    # thin bright outline
    ring = smooth(0.012, 0.0, np.abs(np.sqrt(r2) - 1.0)) * 0.35
    img = img + GLASS * ring[..., None]
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), 'RGB')


def main():
    os.makedirs(OUT, exist_ok=True)
    full = render()
    for size, name in [(1024, 'icon-1024.png'), (512, 'icon-512.png'), (192, 'icon-192.png'),
                       (180, 'apple-touch-icon.png'), (32, 'favicon-32.png')]:
        full.resize((size, size), Image.LANCZOS).save(os.path.join(OUT, name), optimize=True)
    render(pad=0.1).resize((512, 512), Image.LANCZOS).save(os.path.join(OUT, 'icon-maskable-512.png'), optimize=True)
    print('wrote', ', '.join(sorted(os.listdir(OUT))))


if __name__ == '__main__':
    main()
