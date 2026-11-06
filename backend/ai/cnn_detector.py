"""Computer-vision board-state extraction (OpenCV + PyTorch CNN).

Pipeline for a photo or video frame of a physical carrom board:

 1. lighting normalisation   CLAHE on the L channel (hue untouched)
 2. board detection          Canny -> largest 4-corner contour (min-area-rect fallback)
 3. perspective warp         top-down square in board units (740 x 740 -> 1 px = 1 unit)
 4. coin proposals          per-colour masks -> distance-transform peaks (separates touching coins)
 5. CNN verification         small CNN on 32x32 patches (white/black/queen/striker/background)
                             confirms or corrects each proposal; skipped when torch/weights are absent
 6. state export             coin positions in board units, ids matching the physics engine

Everything except step 5 works with OpenCV + NumPy only. The CNN can be
trained on synthetic renders (`train_cnn`) so no labelled dataset is needed to
bootstrap; fine-tune on real photos before relying on it in the wild.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

try:  # optional dependency
    import torch
    from torch import nn

    TORCH = True
except Exception:  # pragma: no cover
    torch = None  # type: ignore[assignment]
    nn = None  # type: ignore[assignment]
    TORCH = False

BOARD = 740
COIN_R = 15.1
STRIKER_R = 18.0
CLASSES = ("white", "black", "queen", "striker", "background")
PATCH = 32


# ------------------------------------------------------------------------- CNN
if TORCH:

    class CoinPatchCNN(nn.Module):  # type: ignore[misc]
        """3x conv(+BN+ReLU+pool) -> 2 FC. Input: (N, 3, 32, 32) in [0, 1]."""

        def __init__(self, n_classes: int = len(CLASSES)) -> None:
            super().__init__()

            def block(i: int, o: int):
                return nn.Sequential(nn.Conv2d(i, o, 3, padding=1), nn.BatchNorm2d(o), nn.ReLU(),
                                     nn.MaxPool2d(2))

            self.features = nn.Sequential(block(3, 16), block(16, 32), block(32, 64))
            self.head = nn.Sequential(nn.Flatten(), nn.Linear(64 * 4 * 4, 64), nn.ReLU(),
                                      nn.Dropout(0.2), nn.Linear(64, n_classes))

        def forward(self, x):
            return self.head(self.features(x))


# ---------------------------------------------------------------- result types
@dataclass
class Detection:
    label: str
    x: float
    y: float
    radius: float
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.label, "x": round(self.x, 1), "y": round(self.y, 1),
                "radius": round(self.radius, 1), "confidence": round(self.confidence, 2)}


@dataclass
class DetectionResult:
    detections: list[Detection]
    board_corners: np.ndarray | None
    board_found: bool
    warped: np.ndarray | None = None
    baselines: dict[str, float] = field(default_factory=lambda: {"p1_y": 620.0, "p2_y": 120.0})

    @property
    def coins(self) -> list[Detection]:
        return [d for d in self.detections if d.label in ("white", "black", "queen")]

    @property
    def striker(self) -> Detection | None:
        s = [d for d in self.detections if d.label == "striker"]
        return max(s, key=lambda d: d.confidence) if s else None


# -------------------------------------------------------------------- detector
class BoardDetector:
    def __init__(self, weights_path: str | None = None, size: int = BOARD) -> None:
        self.size = size
        self.net = None
        path = weights_path or os.getenv("CARROM_CNN_WEIGHTS")
        if TORCH and path and os.path.exists(path):
            self.net = CoinPatchCNN()
            self.net.load_state_dict(torch.load(path, map_location="cpu"))
            self.net.eval()

    # 1 -------------------------------------------------------------------
    @staticmethod
    def normalize_lighting(img: np.ndarray) -> np.ndarray:
        # CLAHE on luminance only: evens out exposure without shifting hue. (Gray-world white
        # balance was tried and rejected: a mostly-tan board tints the white coins blue.)
        lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
        lab[..., 0] = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(lab[..., 0])
        return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)

    # 2 -------------------------------------------------------------------
    @staticmethod
    def _order(pts: np.ndarray) -> np.ndarray:
        pts = pts.reshape(4, 2).astype(np.float32)
        s, d = pts.sum(axis=1), np.diff(pts, axis=1).ravel()
        return np.array([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]],
                        dtype=np.float32)  # tl, tr, br, bl

    def find_board(self, img: np.ndarray) -> np.ndarray | None:
        gray = cv2.GaussianBlur(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), (5, 5), 0)
        edges = cv2.dilate(cv2.Canny(gray, 40, 120), np.ones((3, 3), np.uint8), iterations=1)
        cnts, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not cnts:
            return None
        img_area = img.shape[0] * img.shape[1]
        for c in sorted(cnts, key=cv2.contourArea, reverse=True)[:5]:
            if cv2.contourArea(c) < 0.12 * img_area:
                break
            approx = cv2.approxPolyDP(c, 0.02 * cv2.arcLength(c, True), True)
            if len(approx) == 4 and cv2.isContourConvex(approx):
                return self._order(approx)
            box = cv2.boxPoints(cv2.minAreaRect(c))
            return self._order(box)
        return None

    # 3 -------------------------------------------------------------------
    def warp(self, img: np.ndarray, corners: np.ndarray) -> np.ndarray:
        s = self.size - 1
        dst = np.array([[0, 0], [s, 0], [s, s], [0, s]], dtype=np.float32)
        return cv2.warpPerspective(img, cv2.getPerspectiveTransform(corners, dst), (self.size, self.size))

    # 4 -------------------------------------------------------------------
    def segment(self, warped: np.ndarray, margin: int = 10) -> tuple[dict[str, np.ndarray], np.ndarray]:
        """Per-colour foreground masks on the top-down board (board colour = image median)."""
        board_bgr = np.median(warped.reshape(-1, 3), axis=0)
        hsv = cv2.cvtColor(warped, cv2.COLOR_BGR2HSV)
        h, sat, val = hsv[..., 0], hsv[..., 1], hsv[..., 2]
        fg = np.linalg.norm(warped.astype(np.float32) - board_bgr, axis=2) > 45
        fg[:margin], fg[-margin:], fg[:, :margin], fg[:, -margin:] = False, False, False, False
        # thresholds are relative to the board's own colour so they survive exposure changes
        b_h, b_s, b_v = (float(x) for x in np.median(hsv[10:-10, 10:-10].reshape(-1, 3), axis=0))
        red = ((h <= 10) | (h >= 165)) & (sat > 0.8 * max(b_s, 60)) & (val > 0.35 * b_v)
        masks = {
            "queen": fg & red,
            "black": fg & (val < 0.38 * b_v) & ~red,
            "white": fg & (val > 0.92 * b_v) & (sat < 0.45 * max(b_s, 60)) & ~red,
        }
        masks["other"] = fg & ~masks["queen"] & ~masks["black"] & ~masks["white"]
        return masks, board_bgr

    def propose_circles(self, warped: np.ndarray) -> list[tuple[str, float, float, float]]:
        """Distance-transform peaks per colour mask -> (colour, x, y, radius).

        Hough circles fall apart on the tightly packed opening rosette; peaks of the distance
        transform of an eroded per-colour mask separate touching coins reliably.
        """
        k = self.size / BOARD
        masks, _ = self.segment(warped)
        out: list[tuple[str, float, float, float]] = []
        kernel = np.ones((3, 3), np.uint8)
        win = int(7 * k) * 2 + 1
        for colour, m in masks.items():
            m8 = cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_OPEN, kernel)
            dt = cv2.GaussianBlur(cv2.distanceTransform(m8, cv2.DIST_L2, 5), (3, 3), 0)
            peaks = (dt == cv2.dilate(dt, np.ones((win, win), np.uint8))) & (dt >= 8 * k)
            ys, xs = np.nonzero(peaks)
            for x, y in zip(xs, ys):
                r = float(dt[y, x]) + (0.3 if colour == "black" else 1.5 * k)
                out.append((colour, float(x) / k, float(y) / k, r / k))
        return out

    def find_striker(self, warped: np.ndarray) -> Detection | None:
        """Striker lives on a baseline, so look for a coin-sized-plus blob in those bands.

        The foreground (anything not board-coloured) is closed to fill speckle, and the blob's
        distance-transform peak must exceed a coin radius, which is robust to the striker's colour.
        """
        masks, _ = self.segment(warped)
        fg = np.zeros(warped.shape[:2], np.uint8)
        for m in masks.values():
            fg |= m.astype(np.uint8)
        fg = cv2.morphologyEx(cv2.morphologyEx(fg, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8)),
                              cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        dt = cv2.distanceTransform(fg, cv2.DIST_L2, 5)
        best: Detection | None = None
        for yb in (620, 120):
            lo, hi = max(0, yb - 25), min(self.size, yb + 26)
            band = dt[lo:hi]
            iy, ix = np.unravel_index(int(np.argmax(band)), band.shape)
            val = float(band[iy, ix])
            if val >= 16.3 and (best is None or val > best.radius):
                best = Detection("striker", float(ix), float(iy + lo), val + 0.8, 0.7)
        return best

    # 5 -------------------------------------------------------------------
    @staticmethod
    def _patch(warped: np.ndarray, x: float, y: float, r: float) -> np.ndarray:
        half = int(max(r, 12) * 1.25)
        xi, yi = int(round(x)), int(round(y))
        pad = cv2.copyMakeBorder(warped, half, half, half, half, cv2.BORDER_REPLICATE)
        crop = pad[yi:yi + 2 * half, xi:xi + 2 * half]
        return cv2.resize(crop, (PATCH, PATCH), interpolation=cv2.INTER_AREA)

    def _classify_cnn(self, patches: list[np.ndarray]) -> list[tuple[str, float]]:
        x = torch.from_numpy(np.stack([cv2.cvtColor(p, cv2.COLOR_BGR2RGB) for p in patches]))
        x = x.permute(0, 3, 1, 2).float() / 255.0
        with torch.no_grad():
            prob = torch.softmax(self.net(x), dim=1)
        conf, idx = prob.max(dim=1)
        return [(CLASSES[int(i)], float(c)) for i, c in zip(idx, conf)]

    # 6 -------------------------------------------------------------------
    def detect(self, img: np.ndarray, assume_top_down: bool = False) -> DetectionResult:
        img = self.normalize_lighting(img)
        corners = None if assume_top_down else self.find_board(img)
        warped = self.warp(img, corners) if corners is not None else cv2.resize(img, (self.size, self.size))
        proposals = self.propose_circles(warped)
        dets: list[Detection] = []
        for colour, x, y, r in proposals:
            if self._in_pocket(x, y):
                continue
            label = colour
            if colour == "other" or r >= (COIN_R + STRIKER_R) / 2 + 0.3:
                label = "striker"          # big or oddly coloured blob = striker
            dets.append(Detection(label, x, y, r, 0.85))
        if self.net is not None and dets:   # CNN verifies / refines every proposal
            labels = self._classify_cnn([self._patch(warped, d.x, d.y, d.radius) for d in dets])
            refined: list[Detection] = []
            for d, (lab, conf) in zip(dets, labels):
                if lab == "background" and conf > 0.8:
                    continue
                if lab != d.label and conf > 0.85:
                    d.label = lab
                d.confidence = max(conf, 0.0) if lab == d.label else d.confidence
                refined.append(d)
            dets = refined
        if not any(d.label == "striker" for d in dets):
            st = self.find_striker(warped)
            if st is not None:
                dets = [d for d in dets if math.hypot(d.x - st.x, d.y - st.y) > 10] + [st]
        return DetectionResult(self._nms(dets), corners, corners is not None, warped)

    @staticmethod
    def _in_pocket(x: float, y: float) -> bool:
        return any(math.hypot(x - px, y - py) < 24 for px in (35, BOARD - 35) for py in (35, BOARD - 35))

    @staticmethod
    def _nms(dets: list[Detection]) -> list[Detection]:
        out: list[Detection] = []
        for d in sorted(dets, key=lambda d: -d.confidence):
            if all(math.hypot(d.x - o.x, d.y - o.y) > 0.8 * min(d.radius, o.radius) for o in out):
                out.append(d)
        return out


def to_physics_coins(result: DetectionResult) -> list[dict[str, Any]]:
    """Map detections to the engine's coin ids (w1..w9, b1..b9, q1); unseen coins are pocketed."""
    groups: dict[str, list[Detection]] = {"white": [], "black": [], "queen": []}
    for d in result.coins:
        groups[d.label].append(d)
    out: list[dict[str, Any]] = []
    for kind, prefix, total in (("white", "w", 9), ("black", "b", 9), ("queen", "q", 1)):
        seen = sorted(groups[kind], key=lambda d: (round(d.y / 20), d.x))[:total]
        for i in range(total):
            cid = f"{prefix}{i + 1}"
            if i < len(seen):
                out.append({"id": cid, "type": kind, "x": seen[i].x, "y": seen[i].y, "is_pocketed": False})
            else:
                out.append({"id": cid, "type": kind, "x": 0.0, "y": 0.0, "is_pocketed": True})
    return out


# --------------------------------------------------- synthetic data & training
def render_board(coins: list[tuple[str, float, float]], striker: tuple[float, float] | None = None,
                 canvas: int = 960, perspective: float = 0.0, brightness: float = 1.0,
                 noise: float = 0.0, seed: int = 0) -> np.ndarray:
    """Render a synthetic photo: tan board on a dark table, optional perspective/lighting/noise."""
    rng = np.random.default_rng(seed)
    top = np.full((BOARD, BOARD, 3), (120, 170, 205), np.uint8)             # tan board (BGR)
    cv2.rectangle(top, (0, 0), (BOARD - 1, BOARD - 1), (40, 70, 110), 14)    # frame
    for px in (35, BOARD - 35):
        for py in (35, BOARD - 35):
            cv2.circle(top, (px, py), 30, (15, 15, 15), -1)
    cv2.circle(top, (BOARD // 2, BOARD // 2), 85, (60, 90, 140), 2)           # centre ring (decoy)
    for ex in (150, BOARD - 150):                                            # baseline end circles (decoys)
        for ey in (120, BOARD - 120):
            cv2.circle(top, (ex, ey), 17, (60, 90, 140), 2)
    palette = {"white": (235, 240, 245), "black": (30, 30, 30), "queen": (40, 40, 200)}
    for kind, x, y in coins:
        cv2.circle(top, (int(x), int(y)), int(COIN_R), palette[kind], -1)
        cv2.circle(top, (int(x), int(y)), int(COIN_R), (20, 40, 70), 2)
    if striker is not None:
        cv2.circle(top, (int(striker[0]), int(striker[1])), int(STRIKER_R), (225, 215, 175), -1)
        cv2.circle(top, (int(striker[0]), int(striker[1])), int(STRIKER_R), (20, 40, 70), 2)
    scene = np.full((canvas, canvas, 3), (35, 38, 40), np.uint8)
    m = (canvas - BOARD * 1.0) / 2
    src = np.array([[0, 0], [BOARD, 0], [BOARD, BOARD], [0, BOARD]], np.float32)
    j = perspective * 60
    dst = np.array([[m + j, m + j * 0.6], [m + BOARD - j * 0.5, m], [m + BOARD, m + BOARD - j * 0.4],
                    [m, m + BOARD]], np.float32)
    scene = cv2.warpPerspective(top, cv2.getPerspectiveTransform(src, dst), (canvas, canvas),
                                borderValue=(35, 38, 40))
    scene = np.clip(scene.astype(np.float32) * brightness, 0, 255)
    if noise > 0:
        scene += rng.normal(0, noise, scene.shape)
    return np.clip(scene, 0, 255).astype(np.uint8)


def synthetic_patches(n: int, seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Return (N,32,32,3) uint8 patches and integer labels for CNN training."""
    rng = np.random.default_rng(seed)
    det = BoardDetector()
    xs: list[np.ndarray] = []
    ys: list[int] = []
    while len(xs) < n:
        kinds = [("white", "black", "queen")[rng.integers(0, 3)] for _ in range(6)]
        coins = [(k, float(rng.uniform(80, 660)), float(rng.uniform(80, 660))) for k in kinds]
        striker = (float(rng.uniform(150, 590)), 620.0)
        img = render_board(coins, striker, canvas=BOARD, brightness=float(rng.uniform(0.7, 1.2)),
                           noise=float(rng.uniform(0, 8)), seed=int(rng.integers(1 << 30)))
        for kind, x, y in coins:
            xs.append(det._patch(img, x, y, COIN_R)), ys.append(CLASSES.index(kind))
        xs.append(det._patch(img, *striker, STRIKER_R)), ys.append(CLASSES.index("striker"))
        for _ in range(3):  # background patches (empty board, centre ring decoys, end circles)
            bx, by = float(rng.uniform(60, 680)), float(rng.uniform(60, 680))
            if all(math.hypot(bx - x, by - y) > 40 for _, x, y in coins):
                xs.append(det._patch(img, bx, by, COIN_R)), ys.append(CLASSES.index("background"))
    return np.stack(xs[:n]), np.array(ys[:n])


def train_cnn(epochs: int = 8, n: int = 4000, save_path: str = "cnn_coin.pt", seed: int = 0):  # pragma: no cover
    if not TORCH:
        raise RuntimeError("torch is required for training")
    torch.manual_seed(seed)
    xs, ys = synthetic_patches(n, seed)
    x = torch.from_numpy(xs[..., ::-1].copy()).permute(0, 3, 1, 2).float() / 255.0
    y = torch.from_numpy(ys).long()
    net = CoinPatchCNN()
    opt = torch.optim.AdamW(net.parameters(), lr=2e-3)
    for ep in range(epochs):
        perm = torch.randperm(len(x))
        net.train()
        for i in range(0, len(x), 64):
            idx = perm[i:i + 64]
            loss = nn.functional.cross_entropy(net(x[idx]), y[idx])
            opt.zero_grad()
            loss.backward()
            opt.step()
    torch.save(net.state_dict(), save_path)
    return net
