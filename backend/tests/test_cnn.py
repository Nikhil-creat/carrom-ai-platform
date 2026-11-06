import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from ai.cnn_detector import BoardDetector, render_board, to_physics_coins  # noqa: E402
from physics import CarromPhysics  # noqa: E402


def _layout():
    p = CarromPhysics()
    coins = [(b.kind, b.x, b.y) for b in p.bodies if b.kind != "striker"]
    return p, coins


def _match(result, coins, tol):
    """Fraction of ground-truth coins with a same-class detection within tol units."""
    hit = 0
    for kind, x, y in coins:
        if any(d.label == kind and math.hypot(d.x - x, d.y - y) <= tol for d in result.coins):
            hit += 1
    return hit / len(coins)


def test_topdown_rosette_detected():
    _, coins = _layout()
    img = render_board(coins, (370, 620), canvas=740)
    res = BoardDetector().detect(img, assume_top_down=True)
    assert res.striker is not None and abs(res.striker.x - 370) < 3 and abs(res.striker.y - 620) < 3
    assert _match(res, coins, 4.0) >= 0.85


def test_perspective_photo_with_lighting_and_noise():
    kinds = ["white", "black", "queen", "white", "black", "white", "black", "white", "black", "white"]
    scattered = [(k, 150.0 + 110.0 * (i % 5), 200.0 + 140.0 * (i // 5)) for i, k in enumerate(kinds)]
    img = render_board(scattered, (300, 620), canvas=960, perspective=0.5, brightness=0.8, noise=4.0)
    res = BoardDetector().detect(img)
    assert res.board_found
    assert _match(res, scattered, 7.0) >= 0.8
    assert res.striker is not None and abs(res.striker.x - 300) < 6 and abs(res.striker.y - 620) < 6
    assert not any(d.label in ("white", "black", "queen") and d.radius > 17 for d in res.detections)


def test_export_to_physics_ids():
    _, coins = _layout()
    img = render_board(coins[:7], canvas=740)
    res = BoardDetector().detect(img, assume_top_down=True)
    out = to_physics_coins(res)
    assert len(out) == 19
    assert sum(1 for c in out if not c["is_pocketed"]) >= 5
    phys = CarromPhysics()
    phys.load_coins(out)


def test_no_phantom_striker_on_full_rosette():
    _, coins = _layout()
    res = BoardDetector().detect(render_board(coins, None, canvas=740), assume_top_down=True)
    assert res.striker is None
