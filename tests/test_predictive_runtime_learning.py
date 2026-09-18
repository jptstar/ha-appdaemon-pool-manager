import importlib.util
import sys
from pathlib import Path


MODULE_DIR = Path(__file__).parents[1] / "apps" / "pool_manager"
sys.path.insert(0, str(MODULE_DIR))
MODULE_PATH = MODULE_DIR / "pool_predictive_runtime.py"

spec = importlib.util.spec_from_file_location("pool_predictive_runtime", MODULE_PATH)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class _Runtime(module.PredictiveHeatingSupport):
    pass


def test_thermal_learning_is_persisted_and_reloaded(tmp_path):
    path = tmp_path / "thermal.json"

    app = object.__new__(_Runtime)
    app.chauffage_predictif_apprentissage = True
    app.chauffage_predictif_learning_file = str(path)
    app.chauffage_predictif_rate_model = {
        "smart": {"15_20": {"rate": 0.31, "count": 8}}
    }
    app.chauffage_predictif_loss_model = {
        "closed": {"10_15": {"rate": 0.07, "count": 5}}
    }
    app.log = lambda *args, **kwargs: None
    app._save_predictive_learning()

    restored = object.__new__(_Runtime)
    restored.chauffage_predictif_learning_file = str(path)
    restored.chauffage_predictif_rate_model = {}
    restored.chauffage_predictif_loss_model = {}
    restored.log = lambda *args, **kwargs: None
    restored._load_predictive_learning()

    assert restored.chauffage_predictif_rate_model == app.chauffage_predictif_rate_model
    assert restored.chauffage_predictif_loss_model == app.chauffage_predictif_loss_model
