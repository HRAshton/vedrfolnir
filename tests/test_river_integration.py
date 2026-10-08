"""An integration smoke test of the actual River regression pipeline."""
import importlib.util
import os
import pickle
import pytest


def test_real_river_pipeline_roundtrip():
    if os.getenv("VEDRFOLNIR_TEST_STUB_RIVER") == "1":
        pytest.skip("Explicit local test stub; CI must run the real integration test")
    if importlib.util.find_spec("river") is None:
        pytest.fail("Install River before running the standard test suite")
    from importlib.metadata import version
    from vedrfolnir.learning import build_model
    assert version("river")
    model = build_model()
    features = {"plan_total_jobs": 10.0, "estimated_work": 34.0}
    model.learn_one(features, 3.2)
    restored = pickle.loads(pickle.dumps(model))
    prediction = restored.predict_one(features)
    assert prediction is not None
    assert isinstance(float(prediction), float)
