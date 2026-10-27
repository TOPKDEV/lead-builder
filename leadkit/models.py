from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Listing:
    mls_number: str
    address: str
    market: str = ""          # the market label this listing was found under
    city: str = ""
    state: str = ""
    zip_code: str = ""
    price: float | None = None
    beds: int | None = None
    baths: float | None = None
    sqft: int | None = None
    days_on_market: int | None = None
    property_type: str = ""
    listing_url: str = ""
    agent_name: str | None = None
    agent_phone: str | None = None
    agent_brokerage: str | None = None
    agent_email: str | None = None


@dataclass
class Market:
    """An area to build from, as Realtor.com and Zillow take it: a county,
    city or ZIP code ("Broward County, FL")."""
    label: str

    def as_dict(self) -> dict:
        return {"label": self.label}
