"""
Kadaster adapter skeleton.
Keep credentials server-side.
"""

def get_property_facts(postcode: str, house_number: str, api_key: str):
    # Connect to the official Kadaster service you license.
    return {
        "postcode": postcode,
        "house_number": house_number,
        "build_year": None,
        "floor_area_m2": None,
        "property_type": None,
        "last_sale_price": None,
        "last_sale_date": None,
    }
