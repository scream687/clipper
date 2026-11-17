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
