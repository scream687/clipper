from app.framing import keys_from_samples


def test_steady_face_gives_a_single_key():
    samples = [(t * 0.2, 0.30) for t in range(50)]
    keys = keys_from_samples(samples)
    assert len(keys) == 1 and abs(keys[0].cx - 0.30) < 0.01


def test_small_wobble_is_ignored():
    samples = [(t * 0.2, 0.30 + (0.01 if t % 2 else -0.01)) for t in range(50)]
    assert len(keys_from_samples(samples)) == 1


def test_a_big_move_becomes_a_jump_key():
    samples = [(t * 0.2, 0.25) for t in range(20)] + [(4.0 + t * 0.2, 0.75) for t in range(20)]
    keys = keys_from_samples(samples)
    assert keys[-1].jump and abs(keys[-1].cx - 0.75) < 0.01
    assert 3.6 <= keys[-1].t <= 4.6


def test_a_slow_drift_glides():
    samples = [(t * 0.2, 0.30 + t * 0.004) for t in range(60)]
    keys = keys_from_samples(samples)
    assert len(keys) > 1 and not any(k.jump for k in keys[1:])


def test_missing_faces_hold_the_last_position():
    samples = [(t * 0.2, 0.3) for t in range(10)] + [(2.0 + t * 0.2, None) for t in range(10)] + [(4.0 + t * 0.2, 0.31) for t in range(10)]
    keys = keys_from_samples(samples)
    assert len(keys) == 1


def test_no_faces_at_all_gives_no_keys():
    assert keys_from_samples([(t * 0.2, None) for t in range(30)]) == []


from app.framing import choose_speaker


def two_people(seconds=10, fps=10, switch_at=5.0):
    """Left face talks (mouth moving) before switch_at, right face after."""
    samples = []
    for n in range(int(seconds * fps)):
        t = n / fps
        wiggle = 0.05 if n % 2 else 0.0
        left_open = 0.02 + (wiggle if t < switch_at else 0.0)
        right_open = 0.02 + (wiggle if t >= switch_at else 0.0)
        samples.append((t, [(0.25, 0.04, left_open), (0.75, 0.04, right_open)]))
    return samples


def test_crop_follows_whoever_is_talking():
    chosen = dict(choose_speaker(two_people()))
    assert chosen[2.0] == 0.25 and chosen[8.0] == 0.75


def test_a_brief_interjection_does_not_cause_a_cut():
    samples = two_people(switch_at=99)  # left talks the whole time...
    for i, (t, faces) in enumerate(samples):
        if 4.0 <= t < 4.6:  # ...except right speaks for 0.6 s
            samples[i] = (t, [(0.25, 0.04, 0.02), (0.75, 0.04, 0.02 + (0.05 if i % 2 else 0))])
    xs = {x for _, x in choose_speaker(samples)}
    assert xs == {0.25}


def test_single_face_is_followed_and_empty_frames_stay_empty():
    samples = [(0.0, [(0.4, 0.05, 0.02)]), (0.1, []), (0.2, [(0.42, 0.05, 0.03)])]
    assert choose_speaker(samples) == [(0.0, 0.4), (0.1, None), (0.2, 0.42)]


def test_faceless_shot_after_a_camera_cut_shows_the_whole_frame():
    # Close-up at 0.5 for 3 s, cut to a wide shot nobody's face is found in for 2 s, cut back to a face at 0.3.
    samples = [(n / 10, 0.5) for n in range(30)] + [(n / 10, None) for n in range(30, 50)] + [(n / 10, 0.3) for n in range(50, 80)]
    keys = keys_from_samples(samples, cuts=[3.0, 5.0])
    fit = [k for k in keys if k.fit]
    assert [k.t for k in fit] == [3.0]
    after = [k for k in keys if k.t >= 5.0]
    assert after[0].t == 5.0 and after[0].jump and not after[0].fit and abs(after[0].cx - 0.3) < 0.01


def test_face_lost_without_a_cut_holds_the_crop():
    # Speaker looks down for 2 s in the same shot: keep the crop where it was, no whole-frame flash.
    samples = [(n / 10, 0.5) for n in range(30)] + [(n / 10, None) for n in range(30, 50)] + [(n / 10, 0.5) for n in range(50, 80)]
    assert not any(k.fit for k in keys_from_samples(samples, cuts=[]))


import numpy as np

from app.framing import cam_box


def _gameplay_with_facecam(n=8, seed=1):
    """Grey noise that changes every frame (gameplay) with a bordered box fixed in the top-right corner (the facecam)."""
    rng = np.random.default_rng(seed)
    frames = rng.uniform(60, 120, (n, 270, 480)).astype(np.float32)
    frames[:, 0:70, 370:372] = 255   # left border of the cam, x 370
    frames[:, 68:70, 370:480] = 255  # bottom border, y 68
    return frames


def test_facecam_box_snaps_to_the_still_border_around_a_corner_face():
    faces = [(425.0, 32.0, 28.0)] * 8  # a small face in the cam, every frame
    x, y, w, h = cam_box(_gameplay_with_facecam(), faces)
    assert abs(x * 480 - 373) <= 2 and abs((y + h) * 270 - 65) <= 3 and x + w > 0.97 and y < 0.02


def test_facecam_on_a_side_edge_mid_height_is_found():
    """Streams often pin the cam to the left edge halfway down (CarryMinati), not into a corner."""
    rng = np.random.default_rng(2)
    frames = rng.uniform(60, 120, (8, 270, 480)).astype(np.float32)
    frames[:, 110:112, 0:100] = 255   # top border of the cam, y 110
    frames[:, 180:182, 0:100] = 255   # bottom border, y 180
    frames[:, 110:182, 100:102] = 255  # right border, x 100
    box = cam_box(frames, [(50.0, 145.0, 26.0)] * 8)
    assert box is not None
    x, y, w, h = box
    assert x < 0.02 and abs(y * 270 - 115) <= 4 and abs((y + h) * 270 - 177) <= 3 and abs((x + w) * 480 - 97) <= 3


def test_a_steady_tiny_hud_icon_does_not_beat_the_cam_face():
    faces = [(425.0, 32.0, 28.0)] * 8 + [(470.0, 260.0, 8.0)] * 8  # the cam face, and a little avatar icon in the HUD
    x, y, w, h = cam_box(_gameplay_with_facecam(), faces)
    assert x + w > 0.97 and y < 0.02


def test_no_facecam_without_a_steady_small_corner_face():
    frames = _gameplay_with_facecam()
    assert cam_box(frames, []) is None
    assert cam_box(frames, [(425.0, 32.0, 28.0)] * 2) is None   # seen in too few frames
    assert cam_box(frames, [(240.0, 135.0, 120.0)] * 8) is None  # a big face is the shot itself
    assert cam_box(frames, [(240.0, 135.0, 28.0)] * 8) is None   # small but mid-frame: someone in a wide shot


def test_tiles_cover_the_frame_and_overlap():
    from app.framing import tile_boxes
    boxes = tile_boxes(3, 2)
    assert len(boxes) == 6 and boxes[0][:2] == (0.0, 0.0)
    assert all(abs(x + w - 1) < 1e-9 for x, _, w, _ in boxes[2::3]) and all(abs(y + h - 1) < 1e-9 for _, y, _, h in boxes[3:])
    assert boxes[1][0] < boxes[0][0] + boxes[0][2]  # neighbours overlap
    assert tile_boxes(1, 1) == [(0.0, 0.0, 1.0, 1.0)]


def test_a_face_seen_in_two_tiles_counts_once():
    from app.framing import merge_faces
    assert merge_faces([(0.50, 0.002, 0.1), (0.51, 0.003, 0.1), (0.8, 0.002, 0.2)]) == [(0.51, 0.003, 0.1), (0.8, 0.002, 0.2)]
