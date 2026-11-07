# SPDX-License-Identifier: Apache-2.0
"""Independent geometry checks for binary32 perspective thumbnail framing."""
import copy
import importlib.util
import itertools
import math
from pathlib import Path
import struct
import unittest

SOURCE = Path(__file__).resolve().parents[2] / 'addon/PhoenixFreeCADTools/pft_thumbnail_framing.py'
spec = importlib.util.spec_from_file_location('thumbnail_framing_tests', SOURCE)
framing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(framing)


def camera(**overrides):
    return {"position": (16., 9., 120.), "orientation": (.424708, .17592, .339851, .820473),
            "nearDistance": .1, "farDistance": 1000., "focalDistance": 120.,
            "heightAngle": .6000000238418579, "aspectRatio": 1., "viewportMapping": 3,
            "width": 256, "height": 256, **overrides}


def local(point, pose):
    # Independent quaternion matrix, transposed to map world into camera space.
    q = pose['orientation']; norm = math.sqrt(sum(v*v for v in q))
    x, y, z, w = [v/norm for v in q]
    matrix = ((1-2*y*y-2*z*z, 2*x*y-2*z*w, 2*x*z+2*y*w),
              (2*x*y+2*z*w, 1-2*x*x-2*z*z, 2*y*z-2*x*w),
              (2*x*z-2*y*w, 2*y*z+2*x*w, 1-2*x*x-2*y*y))
    delta = [v-p for v, p in zip(point, pose['position'])]
    return tuple(sum(matrix[j][i]*delta[j] for j in range(3)) for i in range(3))


class FramingTests(unittest.TestCase):
    BOX = ((0., 0., 0.), (32., 18., 6.))

    def assert_contained(self, plan, values):
        pose = {**values, **plan['settings']}
        tan_y = math.tan(values['heightAngle']/2)
        aspect = values['width']/values['height'] if values['viewportMapping'] == 3 else values['aspectRatio']
        tan_x = tan_y*aspect
        if values['viewportMapping'] == 3 and aspect < 1:
            tan_x, tan_y = tan_x/aspect, tan_y/aspect
        for corner in itertools.product(*zip(*plan['bounds'])):
            x, y, z = local(corner, pose)
            self.assertGreater(-z, 0)
            self.assertLess(abs(x), -z*tan_x)
            self.assertLess(abs(y), -z*tan_y)
            self.assertLessEqual(pose['nearDistance'], -z)
            self.assertGreaterEqual(pose['farDistance'], -z)
        self.assertTrue(framing.verify_perspective(plan, pose))
        for name, value in plan['settings'].items():
            for number in value if isinstance(value, tuple) else (value,):
                self.assertEqual(number, struct.unpack('!f', struct.pack('!f', number))[0], name)

    def test_all_mappings_camera_aspects_and_output_shapes_contain_all_corners(self):
        for mapping, aspect, size in itertools.product(range(5), (.2, 1., 3.),
                                                      ((256, 256), (640, 240), (160, 640), (1, 1024), (1024, 1))):
            with self.subTest(mapping=mapping, aspect=aspect, size=size):
                values = camera(viewportMapping=mapping, aspectRatio=aspect, width=size[0], height=size[1])
                self.assert_contained(framing.plan_perspective(self.BOX, values), values)

    def test_real_box_crop_replay_and_new_ten_percent_sphere_padding(self):
        values = camera()
        center, radius = (16., 9., 3.), math.sqrt(32**2+18**2+6**2)/2
        # Coin's old fit used r/tan(half-angle). Construct its actual pose with
        # the forward axis from the independent rotation matrix helper.
        rotation = {**values, 'position': (0., 0., 0.)}
        axes = [local(axis, rotation)[2] for axis in ((1., 0., 0.), (0., 1., 0.), (0., 0., 1.))]
        old_distance = radius/math.tan(values['heightAngle']/2)
        old = {**values, 'position': tuple(c+d*old_distance for c, d in zip(center, axes))}
        x, _, z = local((32., 18., 6.), old)
        old_normalized_x = .5 + x/(-z*math.tan(values['heightAngle']/2))/2
        self.assertAlmostEqual(old_normalized_x, 1.025647989650702, places=10)
        self.assertGreater(old_normalized_x, 1)
        plan = framing.plan_perspective(self.BOX, values)
        self.assert_contained(plan, values)
        self.assertGreater(plan['settings']['focalDistance'], 1.1*radius/math.sin(values['heightAngle']/2))
        self.assertEqual(plan['camera']['orientation'], values['orientation'])

    def test_empty_point_flat_and_line_have_distinct_valid_semantics(self):
        self.assertIsNone(framing.plan_perspective(None, camera()))
        for bounds in (((2., 3., 4.), (2., 3., 4.)), ((0., 0., 0.), (32., 18., 0.)),
                       ((0., 0., 0.), (0., 18., 0.))):
            with self.subTest(bounds=bounds):
                plan = framing.plan_perspective(bounds, camera())
                self.assert_contained(plan, camera())
                self.assertEqual(plan['radius'] == 0, bounds[0] == bounds[1])
        point = framing.plan_perspective(((2., 3., 4.), (2., 3., 4.)), camera(focalDistance=17.))
        self.assertAlmostEqual(point['settings']['focalDistance'], 17., places=3)

    def test_geometry_scale_and_nonzero_centers_are_bounded(self):
        for scale in (1e-20, 1e-5, 1., 1e12, 1e30):
            bounds = tuple(tuple((v-12)*scale for v in endpoint) for endpoint in self.BOX)
            plan = framing.plan_perspective(bounds, camera())
            self.assert_contained(plan, camera())

    def test_invalid_inputs_overflow_and_unrepresentable_centers_fail_closed(self):
        invalid_camera = ({'viewportMapping': True}, {'viewportMapping': 5}, {'viewportMapping': -1},
                          {'heightAngle': 0}, {'heightAngle': math.pi}, {'heightAngle': float('nan')},
                          {'heightAngle': 1e-320}, {'aspectRatio': 0}, {'aspectRatio': float('inf')},
                          {'aspectRatio': 1e-320}, {'orientation': (0, 0, 0, 0)},
                          {'orientation': (0, 0, 0, 2)}, {'position': (1, 2, True)},
                          {'focalDistance': 0}, {'focalDistance': True}, {'width': True},
                          {'width': 1025}, {'height': 0}, {'nearDistance': 2**10000})
        for change in invalid_camera:
            with self.subTest(change=list(change)):
                with self.assertRaises(framing.PftThumbnailFramingError):
                    framing.plan_perspective(self.BOX, camera(**change))
        for bounds in (((1., 0., 0.), (0., 1., 1.)), ((False, 0, 0), (1, 1, 1)),
                       ((0, 0, 0), (float('inf'), 1, 1)), ((-3e38, -3e38, -3e38), (3e38, 3e38, 3e38)),
                       ((1e30, 1e30, 1e30), (1e30, 1e30, 1e30)),
                       ((1e20, 1e20, 1e20), (1e20+1e5, 1e20+1e5, 1e20+1e5)),
                       ((0., 0., 0.), (1e-45, 0., 0.))):
            with self.subTest(bounds=bounds):
                with self.assertRaises(framing.PftThumbnailFramingError):
                    framing.plan_perspective(bounds, camera())

    def test_native_readback_cannot_silently_change_pose_clip_or_projection(self):
        values = camera(); plan = framing.plan_perspective(self.BOX, values)
        good = {**values, **plan['settings']}
        for change in ({'position': values['position']}, {'orientation': (0., 0., 0., 1.)},
                       {'nearDistance': good['farDistance']}, {'farDistance': good['nearDistance']},
                       {'focalDistance': 0.}, {'heightAngle': .8}, {'aspectRatio': 2.},
                       {'viewportMapping': 4}, {'width': 128}):
            with self.subTest(change=change):
                with self.assertRaises(framing.PftThumbnailFramingError):
                    framing.verify_perspective(plan, {**good, **change})

    def test_input_is_detached_and_empty_invalid_clip_is_not_accepted(self):
        bounds = [list(endpoint) for endpoint in self.BOX]; values = camera()
        original = copy.deepcopy((bounds, values))
        plan = framing.plan_perspective(bounds, values)
        self.assertEqual((bounds, values), original)
        bounds[0][0] = -100; values['position'] = (0., 0., 0.)
        self.assertEqual(plan['bounds'], self.BOX)
        self.assertEqual(plan['camera']['position'], original[1]['position'])
        with self.assertRaises(framing.PftThumbnailFramingError):
            framing.plan_perspective(None, camera(nearDistance=-1.))


if __name__ == '__main__':
    unittest.main()
