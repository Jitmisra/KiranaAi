"""Fit a WCCN-style whitening for the SqueezeNet embedder on a DETERMINISTIC
synthetic corpus. No bench image is read here — the corpus is generated from a
seeded RNG, and the nuisance ladder (rotation, dim/bright, warm/cool, blur) is
the domain knowledge of what a kirana counter does to a packet, not bench data.

Output: squeeze_whiten.npz with mu (512,) and W (512, 512), where
    v_whitened = W @ (f - mu)
W = (Sw + lam*tr(Sw)/d * I)^(-1/2) — inverse square root of the shrunken
within-image scatter, so directions that lighting/rotation/blur move are
divided down and directions that only identity changes keep their length.
"""
import os

import cv2
import numpy as np

SP = os.path.dirname(os.path.abspath(__file__))
LAYER = "onnx_node!squeezenet0_concat7"
SIDE = int(os.environ.get("SQ_SIDE", "224"))
MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)

net = cv2.dnn.readNetFromONNX(os.path.join(SP, "squeezenet1.1-7.onnx"),
                              cv2.dnn.ENGINE_CLASSIC)

rng = np.random.default_rng(20260901)


# ---------------------------------------------------------------- base images

def _colour():
    return rng.integers(0, 256, size=3).astype(np.float32)


def base_image(kind: int, s: int = 96) -> np.ndarray:
    img = np.zeros((s, s, 3), np.float32)
    if kind == 0:      # two-tone split at a random angle
        c1, c2 = _colour(), _colour()
        ang = rng.uniform(0, np.pi)
        yy, xx = np.mgrid[0:s, 0:s]
        m = ((xx - s / 2) * np.cos(ang) + (yy - s / 2) * np.sin(ang)) > rng.uniform(-s / 6, s / 6)
        img[m] = c1
        img[~m] = c2
    elif kind == 1:    # stripes
        c1, c2 = _colour(), _colour()
        period = rng.integers(6, 30)
        ang = rng.uniform(0, np.pi)
        yy, xx = np.mgrid[0:s, 0:s]
        ph = (xx * np.cos(ang) + yy * np.sin(ang)) / period
        m = np.mod(ph, 1.0) < rng.uniform(0.3, 0.7)
        img[m] = c1
        img[~m] = c2
    elif kind == 2:    # blobs / "logo" shapes on a ground
        img[:] = _colour()
        for _ in range(rng.integers(2, 7)):
            cc = _colour()
            x, y = rng.integers(0, s, 2)
            ax_, ay_ = rng.integers(4, s // 2, 2)
            th = rng.uniform(0, 180)
            cv2.ellipse(img, (int(x), int(y)), (int(ax_), int(ay_)),
                        float(th), 0, 360, cc.tolist(), -1)
    elif kind == 3:    # smooth low-frequency field (sum of sinusoids)
        yy, xx = np.mgrid[0:s, 0:s].astype(np.float32) / s
        for ch in range(3):
            acc = np.zeros((s, s), np.float32)
            for _ in range(4):
                fx, fy = rng.uniform(0.5, 4.0, 2)
                phx, phy = rng.uniform(0, 2 * np.pi, 2)
                acc += rng.uniform(0.2, 1.0) * np.sin(2 * np.pi * fx * xx + phx) \
                    * np.sin(2 * np.pi * fy * yy + phy)
            acc = (acc - acc.min()) / (float(acc.max() - acc.min()) + 1e-6)
            lo, hi = sorted(rng.uniform(0, 255, 2))
            img[:, :, ch] = lo + acc * (hi - lo)
    elif kind == 4:    # text-like rows of small dark rectangles
        img[:] = _colour() * 0.5 + 128
        ink = _colour() * 0.3
        y = int(rng.integers(4, 16))
        while y < s - 8:
            x = int(rng.integers(2, 12))
            hgt = int(rng.integers(4, 9))
            while x < s - 6:
                wdt = int(rng.integers(3, 12))
                if rng.uniform() < 0.8:
                    cv2.rectangle(img, (x, y), (min(x + wdt, s - 1), y + hgt),
                                  ink.tolist(), -1)
                x += wdt + int(rng.integers(2, 6))
            y += hgt + int(rng.integers(3, 10))
    else:              # checker / grid packets
        c1, c2 = _colour(), _colour()
        p = int(rng.integers(8, 32))
        yy, xx = np.mgrid[0:s, 0:s]
        m = ((xx // p) + (yy // p)) % 2 == 0
        img[m] = c1
        img[~m] = c2
    return np.clip(img, 0, 255).astype(np.uint8)


# ------------------------------------------------------------ nuisance ladder

def _rotd(img: np.ndarray, deg: float) -> np.ndarray:
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), deg, 1.0)
    return cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_REPLICATE)


def _gain(img: np.ndarray, g: float, off: float = 0.0) -> np.ndarray:
    return np.clip(img.astype(np.float32) * g + off, 0, 255).astype(np.uint8)


def _wbal(img: np.ndarray, b: float, g: float, r: float) -> np.ndarray:
    return np.clip(img.astype(np.float32) *
                   np.array([b, g, r], np.float32), 0, 255).astype(np.uint8)


def _lowres(img: np.ndarray, f: float) -> np.ndarray:
    h, w = img.shape[:2]
    small = cv2.resize(img, (max(2, int(w * f)), max(2, int(h * f))),
                       interpolation=cv2.INTER_AREA)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


def _scale(img: np.ndarray, f: float) -> np.ndarray:
    h, w = img.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), 0.0, f)
    return cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_REPLICATE)


def _shift(img: np.ndarray, dx: int, dy: int) -> np.ndarray:
    h, w = img.shape[:2]
    m = np.float32([[1, 0, dx], [0, 1, dy]])
    return cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_REPLICATE)


def variants(img: np.ndarray) -> list[np.ndarray]:
    out = [img]
    for deg in (10, 25, 40, -20, 90, 180):
        out.append(_rotd(img, deg))
    out.append(_gain(img, 0.55))                       # dim
    out.append(_gain(img, 1.45, 12))                   # bright
    out.append(_wbal(img, 0.75, 0.95, 1.20))           # warm (BGR)
    out.append(_wbal(img, 1.20, 1.00, 0.80))           # cool
    out.append(cv2.GaussianBlur(img, (0, 0), 1.0))     # mild blur
    out.append(cv2.GaussianBlur(img, (0, 0), 2.5))     # heavy blur
    out.append(_lowres(img, 0.45))                     # resolution mismatch
    out.append(_scale(img, 0.85))                      # crop looseness
    out.append(_scale(img, 1.15))
    out.append(_shift(img, 5, -4))                     # registration error
    # combined nuisances — the live frame is never one nuisance at a time
    out.append(_wbal(cv2.GaussianBlur(_rotd(img, 15), (0, 0), 1.2),
                     0.80, 0.97, 1.15))
    out.append(_gain(_rotd(img, -30), 0.65))
    out.append(_lowres(_wbal(img, 1.15, 1.0, 0.85), 0.5))
    return out


# ------------------------------------------------------------------- features

def grayworld(bgr: np.ndarray) -> np.ndarray:
    f = bgr.astype(np.float32)
    means = f.reshape(-1, 3).mean(axis=0)
    g = float(means.mean())
    return np.clip(f * (g / np.maximum(means, 1e-6))[None, None, :], 0, 255)


LOWRES = int(os.environ.get("SQ_LOWRES", "0"))
PREBLUR = float(os.environ.get("SQ_PREBLUR", "0"))


def feat(bgr: np.ndarray) -> np.ndarray:
    if LOWRES > 0:
        h, w = bgr.shape[:2]
        m = max(h, w)
        if m > LOWRES:
            f = LOWRES / m
            bgr = cv2.resize(bgr, (max(2, round(w * f)), max(2, round(h * f))),
                             interpolation=cv2.INTER_AREA)
    f32 = grayworld(bgr)
    rgb = cv2.cvtColor(f32, cv2.COLOR_BGR2RGB)
    rgb = cv2.resize(rgb, (SIDE, SIDE), interpolation=cv2.INTER_LINEAR)
    if PREBLUR > 0:
        rgb = cv2.GaussianBlur(rgb, (0, 0), PREBLUR)
    blob = ((rgb / 255.0 - MEAN) / STD).transpose(2, 0, 1)[None]
    net.setInput(blob)
    fmap = net.forward(LAYER)[0]
    v = fmap.reshape(fmap.shape[0], -1).mean(axis=1)
    return np.sign(v) * np.sqrt(np.abs(v))   # signed sqrt, matches candidate


def main() -> None:
    n_base = 150
    feats: list[list[np.ndarray]] = []
    for i in range(n_base):
        img = base_image(i % 6)
        feats.append([feat(v) for v in variants(img)])
    X = np.array([f for grp in feats for f in grp])          # (n, 512)
    mu = X.mean(axis=0)

    d = X.shape[1]
    Sw = np.zeros((d, d))
    for grp in feats:
        G = np.array(grp) - np.mean(grp, axis=0)
        Sw += G.T @ G / len(grp)
    Sw /= len(feats)

    lam = float(os.environ.get("WH_LAM", "0.1"))
    Sw_sh = Sw + lam * (np.trace(Sw) / d) * np.eye(d)
    evals, evecs = np.linalg.eigh(Sw_sh)
    W = evecs @ np.diag(evals ** -0.5) @ evecs.T

    np.savez(os.path.join(SP, "squeeze_whiten.npz"),
             mu=mu.astype(np.float64), W=W.astype(np.float64))
    print("corpus", X.shape, "lam", lam,
          "eig range", float(evals.min()), float(evals.max()))
    print("saved squeeze_whiten.npz")


if __name__ == "__main__":
    main()
