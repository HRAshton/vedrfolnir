"""Explicit opt-in River stub for constrained local test environments.

CI never enables it: all tests must run against the real installed dependency.
"""
import importlib.util
import os
import sys
import types

if os.getenv("VEDRFOLNIR_TEST_STUB_RIVER") == "1" and importlib.util.find_spec("river") is None:
    river = types.ModuleType("river")

    class FakeModel:
        def __init__(self, *args, **kwargs):
            self.mean = 0.0
            self.count = 0

        def __or__(self, other):
            return self

        def predict_one(self, features):
            return self.mean

        def learn_one(self, features, y):
            self.mean = (self.mean * self.count + y) / (self.count + 1)
            self.count += 1

    river.preprocessing = types.SimpleNamespace(StandardScaler=FakeModel)
    river.linear_model = types.SimpleNamespace(LinearRegression=FakeModel)
    river.optim = types.SimpleNamespace(Adam=FakeModel)
    sys.modules["river"] = river
