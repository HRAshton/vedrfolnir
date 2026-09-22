"""River regression with persisted model checkpoints and delayed plan labels."""
import math
import pickle
from river import preprocessing, linear_model, optim
from .db import ModelCheckpoint, utcnow

MODEL_ID = "plan_eta_river_v1"


def build_model():
    return preprocessing.StandardScaler() | linear_model.LinearRegression(
        optimizer=optim.Adam(0.01), l2=0.01
    )


class PlanRegressor:
    def __init__(self, session):
        state = session.get(ModelCheckpoint, MODEL_ID)
        if state:
            # Never load checkpoint bytes from outside this trusted application's DB.
            self.model = pickle.loads(state.payload)
            self.seen_count = state.seen_count
        else:
            self.model = build_model()
            self.seen_count = 0

    def estimate(self, features, baseline_minutes, min_training=30):
        if self.seen_count < min_training:
            return baseline_minutes, False
        try:
            log_prediction = self.model.predict_one(features)
            if log_prediction is None or not math.isfinite(log_prediction):
                return baseline_minutes, False
            minutes = max(1.0, min(60 * 24 * 30, math.expm1(min(16, max(0.0, log_prediction)))))
            return minutes, True
        except (ValueError, OverflowError):
            return baseline_minutes, False

    def learn(self, features, actual_remaining_minutes):
        target = math.log1p(max(0.0, actual_remaining_minutes))
        self.model.learn_one(features, target)
        self.seen_count += 1

    def save(self, session):
        checkpoint = session.get(ModelCheckpoint, MODEL_ID)
        if checkpoint is None:
            checkpoint = ModelCheckpoint(name=MODEL_ID, payload=b"", seen_count=0)
            session.add(checkpoint)
        checkpoint.payload = pickle.dumps(self.model, protocol=pickle.HIGHEST_PROTOCOL)
        checkpoint.seen_count = self.seen_count
        checkpoint.updated_at = utcnow()
