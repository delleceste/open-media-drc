"""Draw the mascot wallpapers from scratch: a moonlit larch glade and a dark
gradient in the daemon's hues, with the BSD daemon or Tux. Everything is
procedural; the only inputs are the mascot drawings (Daemon-phk.svg.webp,
Tux.svg.webp). Works in RGB float, writes BGR PNG.

Usage: python3.12 draw-wallpapers.py [artdir] [outdir] [daemon|tux|all] [gradient|larch|both]
Needs numpy and opencv; each image takes a few minutes."""
import sys
import cv2
import numpy as np

W, H = 3840, 2160
ART = sys.argv[1] if len(sys.argv) > 1 else '.'
OUT = sys.argv[2] if len(sys.argv) > 2 else '.'
Y, X = np.mgrid[0:H, 0:W].astype(np.float32)
YN = Y / H


def rgb(*c):
    return np.array(c, np.float32) / 255


def fbm(h, w, scale, octaves=5, seed=0, persist=0.5):
    rng = np.random.default_rng(seed)
    acc = np.zeros((h, w), np.float32)
    amp, tot = 1.0, 0.0
    for i in range(octaves):
        s = max(scale / 2 ** i, 1.0)
        gh, gw = int(h / s) + 3, int(w / s) + 3
        g = rng.standard_normal((gh, gw)).astype(np.float32)
        up = cv2.resize(g, (int(gw * s), int(gh * s)), interpolation=cv2.INTER_CUBIC)
        acc += amp * up[:h, :w]
        tot += amp
        amp *= persist
    acc /= tot
    return (acc - acc.mean()) / (acc.std() + 1e-6)


def fbm1(n, scale, octaves=6, seed=0, persist=0.5):
    return fbm(1, n, scale, octaves, seed, persist)[0]


def smooth(e0, e1, x):
    t = np.clip((x - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


def over(img, color, alpha):
    a = alpha[..., None] if alpha.ndim == 2 else alpha
    return img * (1 - a) + color * a


def poly_mask(polys, shape=(H, W)):
    m = np.zeros(shape, np.uint8)
    for p in polys:
        cv2.fillPoly(m, [np.round(np.asarray(p) * 16).astype(np.int32)], 255, cv2.LINE_AA, shift=4)
    return m.astype(np.float32) / 255


# per-mascot placement: mouth is where the whistle starts (fraction of the bbox), shadows are
# (dx, dy, rx, ry, strength) with dx/rx in widths and dy/ry in px at 1150 px height
MASCOTS = {
    'daemon': dict(file='Daemon-phk.svg.webp', grad_h=1394, mouth=(0.27, 0.415), larch_h=700,
                   shadows=[(-0.2, 45, 0.36, 48, 1.0), (0.3, 20, 0.22, 22, 0.6)], eyes_v=0.42, grass=(-0.6, 0.5)),
    'tux': dict(file='Tux.svg.webp', grad_h=1250, mouth=(0.37, 0.29), larch_h=560,
                shadows=[(-0.05, 30, 0.5, 60, 1.0)], eyes_v=0.27, grass=(-0.55, 0.55)),
}
M = MASCOTS['daemon']


def load_daemon(height):
    d = cv2.imread(f'{ART}/{M["file"]}', cv2.IMREAD_UNCHANGED).astype(np.float32) / 255
    d = np.dstack([d[..., 2], d[..., 1], d[..., 0], d[..., 3]])
    ys, xs = np.nonzero(d[..., 3] > 0)
    d = d[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    s = height / d.shape[0]
    if abs(s - 1) > 1e-3:
        pm = np.dstack([d[..., :3] * d[..., 3:], d[..., 3]])
        pm = cv2.resize(pm, (round(d.shape[1] * s), height),
                        interpolation=cv2.INTER_AREA if s < 1 else cv2.INTER_CUBIC)
        a = np.clip(pm[..., 3:], 0, 1)
        d = np.dstack([np.clip(pm[..., :3] / np.maximum(a, 1e-4), 0, 1), a[..., 0]])
    return d


def place(img, d, cx, bottom):
    """Composite RGBA d with its bbox centred on cx and its bottom at `bottom`."""
    h, w = d.shape[:2]
    x0, y0 = int(round(cx - w / 2)), int(round(bottom - h))
    a = d[..., 3:]
    reg = img[y0:y0 + h, x0:x0 + w]
    img[y0:y0 + h, x0:x0 + w] = reg * (1 - a) + d[..., :3] * a
    return x0, y0


def finish(img, name, seed):
    rng = np.random.default_rng(seed)
    img = np.clip(img, 0, 1) * 255 + rng.uniform(-0.5, 0.5, img.shape).astype(np.float32)
    out = np.clip(img + 0.5, 0, 255).astype(np.uint8)[..., ::-1]
    cv2.imwrite(f'{OUT}/{name}', out, [cv2.IMWRITE_PNG_COMPRESSION, 9])
    return out


# --------------------------------------------------------------------------- larch glade

def spruce_pts(x, yb, h, w, rng):
    n = max(5, int(h / 16))
    right, left = [], []
    for i in range(1, n + 1):
        t = i / n
        y = yb - h + h * t
        for side, lst in ((1, right), (-1, left)):
            hw = w * 0.5 * t ** 0.9 * (0.75 + 0.5 * rng.random())
            lst.append((x + side * hw, y + h / n * 0.1 * rng.random()))
            if i < n:
                lst.append((x + side * hw * (0.35 + 0.2 * rng.random()), y + h / n * 0.3))
    return [(x, yb - h)] + right + [(x + w * 0.04, yb + 4), (x - w * 0.04, yb + 4)] + left[::-1]


GOLD = [rgb(196, 142, 42), rgb(176, 118, 30), rgb(214, 166, 64), rgb(150, 100, 26), rgb(228, 186, 88)]


def tuft(cv, cx, cy, rng, scale=1.0, bright=1.0, n=7, haze=0.0, hcol=None):
    c = GOLD[rng.integers(len(GOLD))] * bright * (0.55 + 0.45 * rng.random())
    if haze:
        c = c * (1 - haze) + hcol * haze
    col = tuple(float(v) for v in np.clip(c, 0, 1))
    for _ in range(n):
        ang = rng.uniform(0.1, np.pi - 0.1) if rng.random() < 0.8 else rng.uniform(-np.pi, 0)
        L = scale * rng.uniform(5, 13)
        cv2.line(cv, (int(cx * 4), int(cy * 4)),
                 (int((cx + L * np.cos(ang)) * 4), int((cy + L * np.sin(ang)) * 4)),
                 col, 1, cv2.LINE_AA, shift=2)


def larch(cv, x, yb, height, rng, bright=1.0, crown=0.34, crown_bottom=None, trunk_w=None, bark=False,
          haze=0.0, hcol=rgb(84, 104, 136)):
    """A larch: straight tapering trunk, a conical crown of whorled branches that rise near the top and droop
    lower down, feathery golden foliage hanging from them, lit from the moon on the right."""
    apex = yb - height
    cb = crown_bottom if crown_bottom is not None else yb - 0.1 * height
    tw = trunk_w or max(2.0, height * 0.02)
    sc = float(np.clip(height / 2600, 0.3, 1.4))
    # trunk
    ys = np.linspace(max(apex, -200), yb + 40, 200)
    tt = (ys - apex) / height
    xc = x + tw * 0.25 * np.sin(ys / 260 + x) + tw * 0.08 * np.sin(ys / 57)
    half = tw / 2 * np.clip(tt, 0.03, 1) ** 0.7
    poly = np.concatenate([np.stack([xc - half, ys], 1), np.stack([xc + half, ys], 1)[::-1]])
    m = poly_mask([poly])
    if bark:
        xs0, xs1 = int(max(0, x - tw)), int(min(W, x + tw))
        tex = cv2.resize(fbm(H // 6, xs1 - xs0, 20, 4, seed=int(x) + 1), (xs1 - xs0, H)) * 0.6 \
            + fbm(H, xs1 - xs0, 60, 5, seed=int(x)) * 0.4
        xcf = np.interp(np.arange(H), ys, xc)[:, None]
        hf = np.maximum(np.interp(np.arange(H), ys, half)[:, None], 1)
        u = np.clip((np.arange(xs0, xs1)[None, :] - xcf) / hf, -1, 1)
        lit = 0.15 + 0.85 * np.clip(u * 0.55 + 0.45, 0, 1) ** 2.2
        col = (rgb(24, 23, 22)[None, None] + rgb(88, 94, 108)[None, None] * lit[..., None])
        col *= (0.72 + 0.28 * np.tanh(tex))[..., None] * bright
        sl = np.s_[:, xs0:xs1]
        cv[sl] = over(cv[sl], col, m[sl])
    else:
        tc = rgb(30, 27, 24) * bright
        cv[:] = over(cv, tc * (1 - haze) + hcol * haze, m)
    # branches: collect geometry first so the foliage mass can go underneath all the needles
    branches = []
    y = apex + rng.uniform(0.004, 0.012) * height
    while y < cb:
        t = (y - apex) / (cb - apex)
        env = crown * height * 0.5 * t ** 0.72 * (1 - 0.2 * t ** 3)
        for _ in range(rng.integers(1, 4)):
            kind = rng.random()
            side = 1 if rng.random() < 0.5 else -1
            L = env * rng.uniform(0.5, 1.15) * (0.35 if kind < 0.25 else 1.0)   # 25% point at the viewer
            if L < 3:
                continue
            a0 = -0.6 + 0.95 * t + rng.normal(0, 0.15)
            sag, lift = rng.uniform(0.3, 0.8), rng.uniform(0.2, 0.6)
            s = np.linspace(0, 1, 22)
            ang = a0 + sag * s - lift * s ** 3
            dx, dy = np.cos(ang) * side, np.sin(ang)
            yy0 = y + rng.uniform(-6, 6) * sc
            bx = np.interp(yy0, ys, xc) + np.r_[0, np.cumsum(dx[:-1])] * L / 21
            by = yy0 + np.r_[0, np.cumsum(dy[:-1])] * L / 21
            if by.max() < -80 or bx.max() < -80 or bx.min() > W + 80:
                continue
            light = float(np.clip(0.5 + 0.5 * (bx[-1] - x) / (crown * height * 0.5 + 1), 0.15, 1))
            branches.append((bx, by, L, side, light * rng.uniform(0.75, 1.1), kind < 0.25))
        y += float(np.clip(rng.uniform(0.006, 0.016) * height, 4, 40))
    mass = np.zeros((H, W), np.float32)
    for bx, by, L, side, light, front in branches:
        hang = 0.12 * L + 8 * sc
        pts = np.stack([bx, by + hang * 0.35], 1)
        cv2.polylines(mass, [np.round(pts * 4).astype(np.int32)], False, float(0.6 + 0.4 * light),
                      max(1, int(hang * 0.6)), cv2.LINE_AA, shift=2)
    mass = cv2.GaussianBlur(mass, (0, 0), 1.5 * sc + 0.5)
    mcol = rgb(36, 29, 14) * bright
    cv[:] = over(cv, mcol * (1 - haze) + hcol * haze, np.clip(mass, 0, 1) * 0.6)
    for bx, by, L, side, light, front in branches:
        th = max(1, int(round(tw / 10 + 1)))
        wc = rgb(34, 29, 24) * bright * (1 - haze) + hcol * haze
        for i in range(len(bx) - 1):
            cv2.line(cv, (int(bx[i] * 4), int(by[i] * 4)), (int(bx[i + 1] * 4), int(by[i + 1] * 4)),
                     tuple(float(v) for v in wc), max(1, int(th * (1 - i / len(bx)))), cv2.LINE_AA, shift=2)
        s = np.linspace(0, 1, len(bx))
        ss = rng.uniform(0.02, 0.06)
        while ss < 1:
            px, py = np.interp(ss, s, bx), np.interp(ss, s, by)
            if -60 < py < H + 60:
                b = bright * (0.3 + 0.7 * light ** 1.5) * (0.6 + 0.4 * ss) * (1.45 if rng.random() < 0.15 else 1)
                tl = (0.12 * L + 8 * sc) * rng.uniform(0.2, 1.0) * (0.5 + 0.6 * ss)
                ang = np.pi / 2 + side * rng.uniform(-0.15, 0.4)
                ex, ey = px + tl * np.cos(ang), py + tl * np.sin(ang)
                cv2.line(cv, (int(px * 4), int(py * 4)), (int(ex * 4), int(ey * 4)),
                         tuple(float(v) for v in rgb(44, 36, 28) * b), 1, cv2.LINE_AA, shift=2)
                for f in np.arange(0, 1.0001, 6 * sc / max(tl, 6 * sc)):
                    tuft(cv, px + (ex - px) * f + rng.normal(0, 2 * sc), py + (ey - py) * f + rng.normal(0, 2 * sc),
                         rng, sc, b * rng.uniform(0.8, 1.15), haze=haze, hcol=hcol)
                tuft(cv, px, py - 2 * sc, rng, sc * 1.1, b, haze=haze, hcol=hcol)
            ss += rng.uniform(4, 9) * sc / L


def blade(cv, x, y, dep, rng):
    L = 5 + 60 * dep ** 1.3 * rng.uniform(0.6, 1.3)
    ang = -np.pi / 2 + rng.normal(0, 0.28)
    r = rng.random()
    c = rgb(52, 52, 26) if r < 0.55 else rgb(122, 104, 50) if r < 0.85 else rgb(150, 158, 150)
    c = c * (0.45 + 0.55 * rng.random()) * (0.7 + 0.5 * (1 - dep))
    bend = rng.normal(0, 0.2)
    mx, my = x + L * 0.5 * np.cos(ang), y + L * 0.5 * np.sin(ang)
    ex, ey = mx + L * 0.5 * np.cos(ang + bend), my + L * 0.5 * np.sin(ang + bend)
    col = tuple(float(v) for v in np.clip(c, 0, 1))
    cv2.line(cv, (int(x * 4), int(y * 4)), (int(mx * 4), int(my * 4)), col, 1 if dep < 0.6 else 2, cv2.LINE_AA, shift=2)
    cv2.line(cv, (int(mx * 4), int(my * 4)), (int(ex * 4), int(ey * 4)), col, 1, cv2.LINE_AA, shift=2)


def larch_glade():
    rng = np.random.default_rng(7)
    moon = np.array([0.765 * W, 0.19 * H])
    dm = np.hypot(X - moon[0], Y - moon[1])
    mr = 118.0

    # sky
    t = np.clip(YN / 0.6, 0, 1) ** 1.25
    img = rgb(6, 10, 24) * (1 - t[..., None]) + rgb(40, 56, 88) * t[..., None]
    halo = 0.55 * np.exp(-np.clip(dm - mr, 0, None) / 70) + 0.22 * np.exp(-dm / 380) + 0.08 * np.exp(-dm / 1400)
    img += rgb(170, 192, 232) * halo[..., None]

    # stars
    st = np.zeros((H, W), np.float32)
    n = 1400
    sx, sy = rng.integers(0, W, n), (rng.random(n) ** 1.6 * 0.55 * H).astype(int)
    st[sy, sx] = rng.random(n) ** 3 * 2.5
    st = cv2.GaussianBlur(st, (0, 0), 0.9) * 3
    img += rgb(220, 228, 255) * (st * np.clip(1 - halo * 3, 0, 1))[..., None]

    # moon disc
    sl = np.s_[int(moon[1] - mr - 4):int(moon[1] + mr + 4), int(moon[0] - mr - 4):int(moon[0] + mr + 4)]
    maria = np.zeros((H, W), np.float32)
    maria[sl] = cv2.GaussianBlur(smooth(0.2, 1.4, fbm(*maria[sl].shape, 90, 2, seed=5)), (0, 0), 4) * 0.11
    fine = np.zeros((H, W), np.float32)
    fine[sl] = fbm(*fine[sl].shape, 4, 3, seed=6) * 0.015
    limb = np.sqrt(np.clip(1 - (dm / mr) ** 2, 0, 1))
    disc = np.clip(mr - dm + 0.5, 0, 1)
    mcol = rgb(240, 241, 234) * (0.82 + 0.18 * limb[..., None]) * (1 - maria[..., None]) * (1 + fine[..., None])
    img = over(img, mcol, disc)

    # clouds, elongated, silver-lit near the moon
    cn = cv2.resize(fbm(H // 2, W // 3, 260, 6, seed=11), (W, H), interpolation=cv2.INTER_CUBIC)
    band = np.exp(-((YN - 0.27) / 0.16) ** 2) + 0.35 * np.exp(-((YN - 0.08) / 0.06) ** 2)
    dens = smooth(-0.1, 1.3, cn) * band * np.clip((dm - mr * 1.15) / 160, 0, 1)
    dens = np.clip(dens * 1.1, 0, 0.95)
    lit = 0.85 * np.exp(-dm / 520) + 0.12
    edge = np.clip(1 - dens * 1.6, 0, 1) ** 0.6
    ccol = rgb(18, 24, 38)[None, None] + rgb(160, 175, 205)[None, None] * (lit * (0.35 + 0.65 * edge))[..., None]
    img = over(img, ccol, dens)

    # mountains: far jagged range with snow, then a darker nearer ridge
    xs = np.arange(W, dtype=np.float32) / W
    env = (np.exp(-((xs - 0.42) / 0.14) ** 2) + 0.55 * np.exp(-((xs - 0.74) / 0.16) ** 2)
           + 0.35 * np.exp(-((xs - 0.12) / 0.1) ** 2) + 0.12)
    rid = 1 - np.abs(fbm1(W, 700, 7, seed=21))
    rid = (rid - rid.min()) / (rid.max() - rid.min())
    top_far = 0.585 * H - H * (0.05 + 0.26 * env * (0.45 + 0.55 * rid)) + 12 * fbm1(W, 12, 3, seed=22)
    rock = fbm(H, W, 90, 6, seed=23)
    gx = cv2.Sobel(rock, cv2.CV_32F, 1, 0, ksize=5) * 0.02 + cv2.Sobel(rock, cv2.CV_32F, 0, 1, ksize=5) * -0.012
    shade = np.clip(0.55 + gx, 0.1, 1.2)
    depth = (Y - top_far[None, :]) / H
    m_far = np.clip(Y - top_far[None, :] + 0.5, 0, 1)
    rid2 = 1 - np.abs(cv2.resize(fbm(H // 2, W, 60, 6, seed=24), (W, H), interpolation=cv2.INTER_CUBIC))
    hf = cv2.GaussianBlur(rid2, (0, 0), 2.5)
    lam = cv2.Sobel(hf, cv2.CV_32F, 1, 0, ksize=3) * 0.86 - cv2.Sobel(hf, cv2.CV_32F, 0, 1, ksize=3) * 0.5
    face = np.clip(0.58 + lam * (0.28 / (lam.std() + 1e-6)), 0.22, 1.05)
    shade = face * (0.9 + 0.1 * np.tanh(rock))
    snow = smooth(0.5, 0.85, rid2 * 0.55 + 0.6 - depth * 4.5) * np.clip(1 - depth * 4, 0, 1)
    mcolr = rgb(24, 32, 50)[None, None] * shade[..., None]
    mcolr = mcolr * (1 - snow[..., None]) + rgb(108, 122, 156)[None, None] * (0.35 + 0.65 * face[..., None]) * snow[..., None]
    haze = smooth(0.0, 0.2, depth)[..., None]
    mcolr = mcolr * (1 - 0.65 * haze) + rgb(46, 60, 88) * 0.65 * haze
    img = over(img, mcolr, m_far)

    top_near = 0.625 * H - H * (0.025 + 0.07 * (1 - np.abs(fbm1(W, 500, 6, seed=31)))) \
        - 0.05 * H * np.exp(-((xs - 0.0) / 0.15) ** 2) - 0.035 * H * np.exp(-((xs - 1.0) / 0.12) ** 2)
    m_near = np.clip(Y - top_near[None, :] + 0.5, 0, 1)
    ntex = 0.8 + 0.2 * np.tanh(fbm(H, W, 14, 4, seed=32))
    img = over(img, rgb(14, 20, 30)[None, None] * ntex[..., None], m_near)

    # distant spruce forest rows veiled by fog
    def fog(y0, sig, a, seed, col=rgb(84, 104, 136)):
        fn = cv2.resize(fbm(H // 4, W // 8, 60, 5, seed=seed), (W, H), interpolation=cv2.INTER_CUBIC)
        return np.clip(np.exp(-((YN - y0) / sig) ** 2) * a * (0.6 + 0.4 * np.tanh(fn)), 0, 1), col

    polys = [spruce_pts(x, rng.uniform(0.605, 0.64) * H, rng.uniform(35, 105), rng.uniform(18, 40), rng)
             for x in rng.uniform(-50, W + 50, 700)]
    img = over(img, rgb(14, 22, 30), poly_mask(polys))
    a, c = fog(0.625, 0.03, 0.75, 41)
    img = over(img, c, a)
    polys = [spruce_pts(x, rng.uniform(0.655, 0.695) * H, rng.uniform(110, 300), rng.uniform(45, 100), rng)
             for x in rng.uniform(-80, W + 80, 170)]
    img = over(img, rgb(9, 15, 20), poly_mask(polys))
    a, c = fog(0.675, 0.028, 0.55, 42)
    img = over(img, c, a)

    # meadow
    yg = (0.705 * H + 0.012 * H * fbm1(W, 400, 4, seed=51)
          - 0.05 * H * np.exp(-((xs - 0.97) / 0.07) ** 2) - 0.02 * H * np.exp(-((xs - 0.02) / 0.06) ** 2))
    gm = np.clip(Y - yg[None, :] + 0.5, 0, 1)
    gd = np.clip((Y - yg[None, :]) / (H - yg[None, :]), 0, 1)
    patch = 0.75 + 0.25 * np.tanh(cv2.resize(fbm(H // 4, W // 4, 50, 5, seed=52), (W, H)))
    gcol = (rgb(44, 46, 30)[None, None] * (1 - gd[..., None]) + rgb(16, 16, 9)[None, None] * gd[..., None]) * patch[..., None]
    img = over(img, gcol, gm)
    a, c = fog(0.715, 0.018, 0.45, 43, rgb(92, 110, 138))
    img = over(img, c, a)

    # mid-distance golden larches
    for x, h in [(0.215, 540), (0.245, 400), (0.27, 300), (0.6, 280), (0.635, 380), (0.665, 470), (0.705, 590),
                 (0.74, 330), (0.84, 500), (0.87, 360), (0.905, 300), (0.55, 210), (0.52, 160), (0.47, 180)]:
        yb = np.interp(x * W, np.arange(W), yg) + 18
        larch(img, x * W + rng.normal(0, 15), yb, h * rng.uniform(0.9, 1.1), rng, bright=0.8, crown=0.3,
              haze=float(np.clip(0.45 - h / 1500, 0.05, 0.4)))

    # grass
    cv = np.clip(img, 0, 1)
    ng = 110000
    gx_ = rng.uniform(0, W, ng)
    ygx = np.interp(gx_, np.arange(W), yg)
    gy_ = ygx + (H - ygx + 30) * rng.random(ng) ** 0.8
    order = np.argsort(gy_)
    for i in order:
        blade(cv, gx_[i], gy_[i], (gy_[i] - ygx[i]) / (H - ygx[i]), rng)
    # frost glints
    fl = np.zeros((H, W), np.float32)
    n = 6000
    fx = rng.integers(0, W, n)
    fy = np.clip(np.interp(fx, np.arange(W), yg) + rng.random(n) ** 0.7 * (H - 0.7 * H), 0, H - 1).astype(int)
    fl[fy, fx] = rng.random(n) ** 2 * 3
    cv += rgb(205, 218, 240) * cv2.GaussianBlur(fl, (0, 0), 0.8)[..., None]

    # rocks
    rocks = []
    for cx, cy, rx, ry in [(0.73, 0.83, 170, 70), (0.8, 0.8, 110, 45), (0.93, 0.92, 260, 110),
                           (0.55, 0.955, 150, 55), (0.02, 0.97, 220, 110)]:
        ang = np.linspace(0, 2 * np.pi, 40, endpoint=False)
        rr = 1 + 0.12 * rng.standard_normal(40)
        rr = np.convolve(np.r_[rr[-2:], rr, rr[:2]], np.ones(5) / 5, 'valid')
        px = cx * W + rx * rr * np.cos(ang)
        py = cy * H + ry * rr * np.sin(ang) * np.where(np.sin(ang) > 0, 0.35, 1.0)
        rocks.append((np.stack([px, py], 1), cy * H, ry))
    rt = fbm(H, W, 25, 5, seed=61)
    for pts, cy, ry in rocks:
        m = poly_mask([pts])
        v = np.clip((cy - Y) / ry, -0.4, 1)
        col = rgb(26, 28, 32)[None, None] + rgb(96, 106, 124)[None, None] * (np.clip(v, 0, 1) ** 2 * (0.6 + 0.4 * np.tanh(rt)))[..., None]
        moss = smooth(0.4, 1.0, rt) * np.clip(1 - v, 0, 1)
        col = col * (1 - moss[..., None] * 0.6) + rgb(40, 48, 22) * moss[..., None] * 0.6
        cv = over(cv, col, m)
        sh = cv2.GaussianBlur(poly_mask([pts + [ -30, ry * 0.25]]), (0, 0), 14) * (1 - m)
        cv *= (1 - 0.45 * sh)[..., None]

    # foreground larches (moon upper right: lit on the right side)
    larch(cv, 0.16 * W, H + 40, 3000, rng, bright=0.6, crown=0.24, crown_bottom=0.5 * H, trunk_w=75, bark=True)
    larch(cv, 0.045 * W, H + 60, 4300, rng, bright=1.0, crown=0.27, crown_bottom=0.56 * H, trunk_w=200, bark=True)
    larch(cv, 0.967 * W, H + 60, 3800, rng, bright=0.9, crown=0.25, crown_bottom=0.46 * H, trunk_w=130, bark=True)

    # grade: gentle vignette
    r2 = ((X - W / 2) / (W / 2)) ** 2 + ((Y - H * 0.45) / (H * 0.75)) ** 2
    cv *= (1 - 0.28 * np.clip(r2, 0, 1.6) / 1.6)[..., None]
    # the daemon, with a soft contact shadow cast away from the moon
    d = load_daemon(M['larch_h'])
    cx, bottom = 0.415 * W, 0.87 * H
    h, w = d.shape[:2]
    k = h / 1150
    sh = np.zeros((H, W), np.float32)
    for dx, dy, rx, ry, st in M['shadows']:
        cv2.ellipse(sh, (int(cx + dx * w), int(bottom - dy * k)), (int(w * rx), int(ry * k)), -3, 0, 360, st, -1, cv2.LINE_AA)
    sh = cv2.GaussianBlur(sh, (0, 0), 22 * k)
    cv *= (1 - 0.6 * sh)[..., None]
    x0, y0 = int(round(cx - w / 2)), int(round(bottom - h))
    a = d[..., 3]
    # a faint red presence bleeding into the night air
    aura = np.zeros((H, W), np.float32)
    aura[y0:y0 + h, x0:x0 + w] = a
    aura = cv2.GaussianBlur(aura, (0, 0), 80) * 0.25
    cv = 1 - (1 - cv) * (1 - np.clip(aura, 0, 1)[..., None] * rgb(150, 10, 5))
    # night grade: darker and cooler, lit from the moon (upper right), eyes kept bright
    c = d[..., :3]
    lum = (c @ np.array([0.3, 0.59, 0.11], np.float32))[..., None]
    c = c * 0.78 + lum * rgb(120, 150, 235) * 0.22
    v, u = np.mgrid[0:h, 0:w].astype(np.float32)
    light = 0.36 + 0.3 * (u / w) + 0.16 * (1 - v / h)
    eyes = ((d[..., :3].min(-1) > 0.8) & (v < M['eyes_v'] * h)).astype(np.float32)
    light = np.maximum(light, cv2.GaussianBlur(eyes, (0, 0), 1.2) * 0.82)
    c = c * light[..., None]
    shifted = np.zeros_like(a)
    shifted[6:, :-6] = a[:-6, 6:]
    rim = cv2.GaussianBlur(np.clip(a - shifted, 0, 1), (0, 0), 1.5)
    c = c + rim[..., None] * rgb(150, 175, 255) * 0.55
    place(cv, np.dstack([np.clip(c, 0, 1), a]), cx, bottom)
    # ground mist drifting over the shoes
    mn = cv2.resize(fbm(H // 4, W // 8, 40, 5, seed=71), (W, H), interpolation=cv2.INTER_CUBIC)
    mist = np.exp(-((Y - bottom + 15) / 38) ** 2) * np.exp(-((X - cx) / 650) ** 2) * (0.55 + 0.45 * np.tanh(mn)) * 0.38
    cv = over(cv, rgb(92, 108, 136), np.clip(mist, 0, 1))
    # a few grass blades in front of the shoes
    for _ in range(1400):
        x = rng.uniform(cx + w * M['grass'][0], cx + w * M['grass'][1])
        y = rng.uniform(bottom - 18, bottom + 30)
        ygx = np.interp(x, np.arange(W), yg)
        blade(cv, x, y, (y - ygx) / (H - ygx), rng)

    return finish(cv, f'{MNAME}-moonlit-larch-glade-4k.png', 1)



# --------------------------------------------------------------------------- musical notes

def _ellipse(cx, cy, ax, ay, ang, n=40):
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    x, y = ax * np.cos(t), ay * np.sin(t)
    c, s = np.cos(ang), np.sin(ang)
    return np.stack([cx + x * c - y * s, cy + x * s + y * c], 1)


def _rect(x0, y0, x1, y1, th):
    d = np.array([x1 - x0, y1 - y0], np.float32)
    n = np.array([-d[1], d[0]]) / (np.hypot(*d) + 1e-6) * th / 2
    return np.array([[x0, y0] + n, [x1, y1] + n, [x1, y1] - n, [x0, y0] - n])


def _flag(x, y, k=1.0):
    t = np.linspace(0, 1, 16)[:, None]
    outer = (1 - t) ** 2 * [x, y] + 2 * (1 - t) * t * [x + 0.34 * k, y + 0.22 * k] + t ** 2 * [x + 0.3 * k, y + 0.62 * k]
    inner = (1 - t) ** 2 * [x + 0.3 * k, y + 0.62 * k] + 2 * (1 - t) * t * [x + 0.26 * k, y + 0.36 * k] + t ** 2 * [x, y + 0.26 * k]
    return np.concatenate([outer, inner])


def note_polys(kind):
    """Note glyph in local units: head centred at the origin, stem ~1 unit tall, y up is negative."""
    head = lambda x, y: _ellipse(x, y, 0.2, 0.14, -0.4)
    sx = 0.175
    if kind in ('eighth', 'quarter', 'sixteenth'):
        polys = [head(0, 0), _rect(sx, -0.04, sx, -1.0, 0.055)]
        if kind != 'quarter':
            polys.append(_flag(sx - 0.02, -1.0))
        if kind == 'sixteenth':
            polys.append(_flag(sx - 0.02, -0.78, 0.9))
        return polys
    # beamed pair (single or double beam)
    dx, dy = 0.75, -0.18
    polys = [head(0, 0), head(dx, dy), _rect(sx, -0.04, sx, -1.0, 0.055), _rect(dx + sx, dy - 0.04, dx + sx, dy - 1.0, 0.055),
             _rect(sx - 0.03, -1.0 + 0.06, dx + sx + 0.03, dy - 1.0 + 0.06, 0.13)]
    if kind == 'beamed16':
        polys.append(_rect(sx - 0.03, -0.76 + 0.06, dx + sx + 0.03, dy - 0.76 + 0.06, 0.1))
    return polys


def whistle(img, mouth, rng, avoid):
    """A looping trail of air from the lips with notes riding on it, glowing in the daemon's colours."""
    u = np.linspace(0, 1, 1400)[:, None]
    P0, P1, P2 = np.array(mouth), np.array([mouth[0] - 520, mouth[1] - 260]), np.array([0.1 * W, 0.15 * H])
    base = (1 - u) ** 2 * P0 + 2 * (1 - u) * u * P1 + u ** 2 * P2
    th = -2 * np.pi * 2.0 * u[:, 0] - np.pi / 2
    r = 190 * smooth(0.05, 0.5, u[:, 0]) * (0.6 + 0.4 * u[:, 0])
    curve = base + np.stack([r * np.cos(th), r * (np.sin(th) + 1)], 1)

    light = np.zeros((H, W, 3), np.float32)
    fade = (0.25 + 0.75 * (1 - u[:, 0]) ** 0.7) * smooth(0, 0.04, u[:, 0])
    warm = np.stack([np.interp(u[:, 0], [0, 0.5, 1], c) for c in ((1.0, 1.0, 1.0), (0.92, 0.55, 0.25), (0.0, 0.1, 0.05))], 1)
    for k, (off, wgt) in enumerate([(0, 1.0), (7, 0.5), (-9, 0.35), (15, 0.2)]):
        tang = np.gradient(curve, axis=0)
        nrm = np.stack([-tang[:, 1], tang[:, 0]], 1) / (np.linalg.norm(tang, axis=1, keepdims=True) + 1e-6)
        pts = curve + nrm * (off * (0.3 + u))
        for i in range(0, len(pts) - 1, 2):
            col = tuple(float(v) for v in warm[i] * fade[i] * wgt)
            cv2.line(light, (int(pts[i, 0] * 16), int(pts[i, 1] * 16)), (int(pts[i + 2 if i + 2 < len(pts) else i + 1, 0] * 16),
                     int(pts[i + 2 if i + 2 < len(pts) else i + 1, 1] * 16)), col, 2 if k == 0 else 1, cv2.LINE_AA, shift=4)
    # sparkles along the trail
    sp = np.zeros((H, W, 3), np.float32)
    for i in rng.integers(20, len(curve), 240):
        q = curve[i] + rng.normal(0, 25 + 60 * u[i, 0], 2)
        if 0 <= q[0] < W and 0 <= q[1] < H:
            sp[int(q[1]), int(q[0])] += rgb(255, 225, 150) * rng.random() ** 2 * 5 * fade[i]
    light += cv2.GaussianBlur(sp, (0, 0), 1.0) * 2
    img[:] = 1 - (1 - img) * np.exp(-(light + cv2.GaussianBlur(light, (0, 0), 6) * 1.5 + cv2.GaussianBlur(light, (0, 0), 30) * 1.5))

    palette = [rgb(255, 215, 0), rgb(255, 150, 20), rgb(255, 244, 214), rgb(255, 90, 30), rgb(255, 200, 60),
               rgb(40, 220, 90), rgb(255, 170, 40), rgb(60, 235, 235), rgb(255, 230, 120), rgb(255, 120, 0)]
    kinds = ['eighth', 'beamed', 'quarter', 'sixteenth', 'eighth', 'beamed16', 'eighth', 'beamed', 'quarter', 'eighth']
    arc = np.r_[0, np.cumsum(np.hypot(*np.diff(curve, axis=0).T))]
    taken = avoid.copy()
    f, j = 0.05, 0
    while f < 0.97 and j < len(kinds):
        kind = kinds[j]
        i = int(np.searchsorted(arc, f * arc[-1]))
        uu = i / (len(curve) - 1)
        tang = curve[min(i + 3, len(curve) - 1)] - curve[max(i - 3, 0)]
        nrm = np.array([-tang[1], tang[0]]) / (np.hypot(*tang) + 1e-6)
        size = 70 + 110 * uu ** 0.9
        pos = curve[i] + nrm * size * 0.45 * (1 if j % 2 else -1) + [-size * 0.2, size * 0.3]
        ang = rng.uniform(-0.35, 0.3)
        c, s_ = np.cos(ang), np.sin(ang)
        polys = [np.stack([pos[0] + size * (q[:, 0] * c - q[:, 1] * s_), pos[1] + size * (q[:, 0] * s_ + q[:, 1] * c)], 1)
                 for q in (np.asarray(p, np.float32) for p in note_polys(kind))]
        m = poly_mask(polys)
        if (m * taken).max() > 0 or m.sum() < 0.9 * np.abs(m).sum() or \
                any(q[:, 0].min() < 20 or q[:, 1].min() < 20 for q in polys):
            f += 0.006
            continue
        taken = np.maximum(taken, cv2.dilate(m, np.ones((int(size * 0.25),) * 2)))
        f += 0.075
        j += 1
        col = palette[j - 1]
        op = 1.0 - 0.3 * uu
        g = cv2.GaussianBlur(m, (0, 0), size * 0.08) * 0.9 + cv2.GaussianBlur(m, (0, 0), size * 0.3) * 0.8
        img[:] = 1 - (1 - img) * np.exp(-(g[..., None] * col * op))
        core = np.clip(col * 0.75 + 0.25, 0, 1)
        img[:] = over(img, core, m * op)

# --------------------------------------------------------------------------- dark gradient

def bezier(p0, p1, p2, t):
    t = t[:, None]
    return (1 - t) ** 2 * p0 + 2 * (1 - t) * t * p1 + t ** 2 * p2


def gradient():
    rng = np.random.default_rng(3)
    c = np.array([0.5 * W, 0.48 * H])
    d = np.hypot((X - c[0]) / 1.25, Y - c[1])
    img = rgb(5, 2, 3)[None, None] + rgb(85, 4, 2)[None, None] * np.exp(-(d / 800) ** 2)[..., None] \
        + rgb(22, 2, 2)[None, None] * np.exp(-(d / 1900) ** 2)[..., None]
    img += rgb(70, 22, 2) * np.exp(-((Y - 0.93 * H) / 160) ** 2 - ((X - c[0]) / 1500) ** 2)[..., None]

    # light ribbons: fans of thin strokes along quadratic curves, coloured along their length
    ribbons = [  # p0, p1, p2 (fractions of W,H), fan width px, lines, colour stops
        ((-0.05, 0.02), (0.30, 0.45), (0.62, 0.62), 180, 70, [(204, 0, 0), (255, 50, 0), (255, 120, 10)]),
        ((-0.05, 0.50), (0.25, 0.60), (0.55, 0.74), 140, 60, [(160, 0, 0), (230, 30, 0), (255, 110, 0)]),
        ((0.10, 1.02), (0.70, 0.82), (1.05, 0.40), 200, 80, [(230, 40, 0), (255, 150, 0), (255, 215, 0)]),
        ((0.48, 0.93), (0.85, 0.86), (1.05, 0.62), 150, 55, [(0, 120, 30), (0, 170, 0), (0, 230, 220)]),
        ((-0.05, 0.74), (0.30, 0.70), (0.52, 0.79), 90, 45, [(255, 150, 0), (255, 215, 0), (255, 180, 40)]),
        ((0.55, -0.05), (0.80, 0.30), (1.05, 0.22), 160, 45, [(120, 0, 0), (200, 10, 0), (255, 70, 0)]),
    ]
    light = np.zeros((H, W, 3), np.float32)
    sparks = np.zeros((H, W), np.float32)
    spark_col = np.zeros((H, W, 3), np.float32)
    t = np.linspace(0, 1, 500)
    for p0, p1, p2, fan, n, stops in ribbons:
        P = [np.array([a * W, b * H]) for a, b in (p0, p1, p2)]
        base = bezier(*P, t)
        tang = np.gradient(base, axis=0)
        nrm = np.stack([-tang[:, 1], tang[:, 0]], 1)
        nrm /= np.linalg.norm(nrm, axis=1, keepdims=True)
        stops = np.array(stops, np.float32) / 255
        cols = np.stack([np.interp(t, np.linspace(0, 1, len(stops)), stops[:, k]) for k in range(3)], 1)
        prof = np.sin(np.pi * t) ** 1.3                     # ribbons fade in and out at their ends
        layer = np.zeros((H, W), np.float32)
        for j in range(n):
            u = rng.uniform(-1, 1)
            spread = fan * (0.25 + 0.75 * t ** 1.3) * u + 25 * np.sin(2 * np.pi * (t * rng.uniform(0.6, 1.4) + rng.random())) * abs(u)
            pts = base + nrm * spread[:, None]
            lay = np.zeros((H, W), np.uint8)
            cv2.polylines(lay, [np.round(pts * 16).astype(np.int32)], False, 255, 1, cv2.LINE_AA, shift=4)
            layer = np.maximum(layer, lay.astype(np.float32) / 255 * rng.uniform(0.15, 1.0) * (1 - 0.6 * abs(u)))
        # colour and fade by position along the ribbon: nearest sample of the centre curve
        grid = np.zeros((H, W), np.float32)
        idx = np.zeros((H, W), np.float32)
        for k in range(len(t) - 1):
            cv2.line(idx, (int(base[k, 0]), int(base[k, 1])), (int(base[k + 1, 0]), int(base[k + 1, 1])),
                     float(k), int(fan * 2.6) + 80)
        ki = idx.astype(int)
        colmap = cols[ki] * prof[ki][..., None]
        light += layer[..., None] * colmap
        # sparkles scattered through the ribbon
        m = rng.integers(0, len(t), 260)
        sp = base[m] + nrm[m] * (fan * rng.uniform(-1, 1, 260) * (0.25 + 0.75 * t[m] ** 1.3))[:, None]
        ok = (sp[:, 0] >= 0) & (sp[:, 0] < W) & (sp[:, 1] >= 0) & (sp[:, 1] < H)
        sp, m = sp[ok].astype(int), m[ok]
        sparks[sp[:, 1], sp[:, 0]] += rng.random(len(m)) ** 2 * 4 * prof[m]
        spark_col[sp[:, 1], sp[:, 0]] = cols[m] * 0.5 + 0.5

    glow = light * 0.9 + cv2.GaussianBlur(light, (0, 0), 5) * 1.2 + cv2.GaussianBlur(light, (0, 0), 28) * 1.2 \
        + cv2.GaussianBlur(light, (0, 0), 110) * 1.1
    sk = cv2.GaussianBlur(sparks[..., None] * spark_col, (0, 0), 1.2) * 3 + cv2.GaussianBlur(sparks[..., None] * spark_col, (0, 0), 8) * 2
    img = img + glow + sk
    img = 1 - np.exp(-img * 1.0)

    # floor: faint horizon line of light under the daemon
    floor_y = 0.905 * H
    img += rgb(120, 40, 10) * (np.exp(-((Y - floor_y) / 3) ** 2) * np.exp(-((X - W / 2) / 900) ** 2) * 0.35)[..., None]
    img *= (1 - 0.35 * smooth(floor_y, H, Y))[..., None]

    r2 = ((X - W / 2) / (W / 2)) ** 2 + ((Y - H / 2) / (H / 2)) ** 2
    img *= (1 - 0.3 * np.clip(r2 / 2, 0, 1))[..., None]
    dm = load_daemon(M['grad_h'])
    h, w = dm.shape[:2]
    x0, y0 = int(round(W / 2 - w / 2)), int(round(floor_y - h))
    # red aura around the silhouette
    aura = np.zeros((H, W), np.float32)
    aura[y0:y0 + h, x0:x0 + w] = dm[..., 3]
    aura = cv2.GaussianBlur(aura, (0, 0), 18) * 0.5 + cv2.GaussianBlur(aura, (0, 0), 70) * 0.7
    img = 1 - (1 - img) * (1 - np.clip(aura, 0, 1)[..., None] * rgb(255, 45, 0) * 0.55)
    # reflection on the glossy floor
    refl = dm[::-1].copy()
    rh = min(h, H - int(floor_y))
    fade = np.linspace(0.22, 0, rh)[:, None]
    ra = refl[:rh, :, 3] * fade
    reg = img[int(floor_y):int(floor_y) + rh, x0:x0 + w]
    rc = cv2.GaussianBlur(refl[:rh, :, :3], (0, 0), 2.5)
    img[int(floor_y):int(floor_y) + rh, x0:x0 + w] = reg * (1 - ra[..., None]) + rc * ra[..., None]
    # contact shadow
    sh = np.zeros((H, W), np.float32)
    cv2.ellipse(sh, (int(W / 2 - w * 0.1), int(floor_y - 8)), (int(w * 0.36), 26), 0, 0, 360, 1.0, -1, cv2.LINE_AA)
    img *= (1 - 0.6 * cv2.GaussianBlur(sh, (0, 0), 14))[..., None]
    avoid = np.zeros((H, W), np.float32)
    avoid[y0:y0 + h, x0:x0 + w] = dm[..., 3] > 0
    avoid = cv2.dilate(avoid, np.ones((41, 41)))
    whistle(img, (x0 + M['mouth'][0] * w, floor_y - h + M['mouth'][1] * h), rng, avoid)
    place(img, dm, W / 2, floor_y)

    return finish(img, f'{MNAME}-dark-gradient-source.png', 2)


if __name__ == '__main__':
    who = sys.argv[3] if len(sys.argv) > 3 else 'all'
    which = sys.argv[4] if len(sys.argv) > 4 else 'both'
    for MNAME in (MASCOTS if who == 'all' else [who]):
        M = MASCOTS[MNAME]
        for name, fn in (('gradient', gradient), ('larch', larch_glade)):
            if which in ('both', name):
                o = fn()
                cv2.imwrite(f'{OUT}/prev_{MNAME}_{name}.png', cv2.resize(o, (1920, 1080), interpolation=cv2.INTER_AREA))
