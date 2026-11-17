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
