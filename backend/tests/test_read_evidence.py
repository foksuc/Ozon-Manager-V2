import importlib.util
import json
from pathlib import Path
from decimal import Decimal

_spec = importlib.util.spec_from_file_location("read_evidence", Path(__file__).resolve().parents[2] / "scripts/verify_read.py")
module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(module)


def test_shape_never_persists_values_or_unknown_key_names():
    data = {"items": [{"name": "SYNTHETIC PRIVATE NAME", "offer_id": "PRIVATE-ID", "price": Decimal("1899.05"),
                       "SYNTHETIC-SECRET-AS-KEY": "SYNTHETIC-API-KEY"}], "result": {"123456789": "PRIVATE-ID"}}
    encoded = json.dumps(module.shape(data))
    for value in ("SYNTHETIC", "PRIVATE", "1899.05", "123456789"):
        assert value not in encoded
    assert module.shape(data)["items"]["count"] == 1
    assert module.shape(data)["items"]["item"]["price"] == "decimal"


def test_shape_is_depth_and_width_bounded():
    value = {"result": {"items": [{"result": {"result": {"result": {"name": "PRIVATE"}}}}]}}
    assert "PRIVATE" not in json.dumps(module.shape(value))
    assert len(module.shape({str(index): index for index in range(1000)})) <= 40
