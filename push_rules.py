"""
Push notification rule checker skeleton.
"""

def should_notify(property_row, calc, alert):
    if alert["city"] and alert["city"] != property_row["city"]:
        return False
    if property_row["price"] > alert["max_price"]:
        return False
    if calc["profit"] < alert["min_profit"]:
        return False
    if calc["roi"] < alert["min_roi"]:
        return False
    return True
