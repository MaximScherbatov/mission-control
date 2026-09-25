import math
import unittest

from app.curvature_path import dubins_connectors


def _circumradius(a, b, c):
    side_a, side_b, side_c = math.dist(a, b), math.dist(b, c), math.dist(a, c)
    area_twice = abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))
    return side_a * side_b * side_c / (2 * area_twice) if area_twice > 1e-7 else float("inf")


class CurvaturePathTest(unittest.TestCase):
    def test_tight_parallel_passes_need_a_wide_tangent_turn(self):
        radius = 95.0
        start, end = (0.0, 0.0), (0.0, 60.0)
        line = next(dubins_connectors(start, (1.0, 0.0), end, (-1.0, 0.0), radius))
        points = list(line.coords)
        self.assertEqual(points[0], start)
        self.assertEqual(points[-1], end)
        self.assertGreater(line.length, math.pi * radius)
        self.assertGreater((points[1][0] - points[0][0]) / math.dist(points[1], points[0]), .99)
        self.assertLess((points[-1][0] - points[-2][0]) / math.dist(points[-1], points[-2]), -.99)
        self.assertGreaterEqual(min(_circumradius(*points[index:index + 3]) for index in range(len(points) - 2)), radius * .98)

    def test_same_heading_keeps_straight_flight(self):
        line = next(dubins_connectors((0, 0), (1, 0), (100, 0), (1, 0), 95))
        self.assertAlmostEqual(line.length, 100, places=3)


if __name__ == "__main__":
    unittest.main()
