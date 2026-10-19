# File purpose: checks duplicate identifier detection and missing-field profiling helpers.
from medical_device_safety.validation import duplicate_values, field_profile

# Supply repeated and blank identifiers, then verify only the duplicate is returned.
def test_duplicate_identifiers():
    assert duplicate_values(["a", "b", "a", None]) == ["a"]

# Profile populated and absent fields, then check the respective missing counts.
def test_missing_field_profile():
    p = field_profile([{"product_code":"ABC"}, {"product_code":""}], ["product_code", "device_id"])
    assert p["product_code"]["missing"] == 1
    assert p["device_id"]["missing"] == 2
