from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class StrictDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Action(StrictDTO):
    id: str
    name: str
    is_voucher_action: bool | None = None
    freeze_date: str | None = None
    auto_add: bool
    status: Literal["active", "upcoming", "ended", "unknown"]
    start_at: str | None
    end_at: str | None
    auto_add_dates: list[str] = Field(default_factory=list)
    member_count: int | None = None
    candidate_count: int | None = None


class Product(StrictDTO):
    id: str
    action_id: str
    offer_id: str
    name: str
    role: Literal["member", "candidate"]
    price: str | None
    action_price: str | None
    min_price: str | None
    max_price: str | None
    elastic: bool | None
    current_boost: str | None = None
    is_voucher_action: bool | None = None
    freeze_date: str | None = None
    card_price: str | None = None
    card_currency: str | None = None
    auto_add_at: str | None
    product_id: str | None = None
    sku: str | None = None
    currency: str | None = "RUB"
    currency_assumed: bool = False
    money_currencies: dict[str, str | None] = Field(default_factory=dict)
    money_assumed_fields: list[str] = Field(default_factory=list)
    price_min_elastic: str | None = None
    price_max_elastic: str | None = None
    max_discount_price: str | None = None
    min_seller_price: str | None = None
    max_action_price: str | None = None
    minimum_discount: str | None = None
    maximum_discount: str | None = None
    actual_discount: str | None = None
    auto_add_discount: str | None = None


class DiscountFilterRequest(StrictDTO):
    threshold: str = Field(max_length=32)
    items: list[Product] = Field(max_length=50000)


class Stock(StrictDTO):
    id: str
    offer_id: str
    name: str
    warehouse_id: str
    warehouse_name: str
    available: int | None
    reserved: int | None
    present: int | None
    product_id: str | None = None
    sku: str | None = None


class PreviewSource(StrictDTO):
    mode: Literal["MOCK", "LIVE_READ"]
    scenario: Literal["ready", "empty", "partial", "error"]
    fetched_at: str
    action_id: str | None
    action: Action | None = None
    items: list[Product | Stock]


class Catalog(StrictDTO):
    mode: Literal["MOCK", "LIVE_READ"]
    fetched_at: str
    complete: bool
    warnings: list[str]
    actions: list[Action]
    products: list[Product]
    stocks: list[Stock]


class PreviewRequest(StrictDTO):
    kind: Literal["prices", "stocks"]
    ids: list[str] = Field(min_length=1, max_length=1000)
    action_id: str | None = None
    mode: Literal["percent", "fixed", "manual"]
    value: str = Field(max_length=32)
    rounding: Literal["0.01", "1", "10", "100"]
    floor: str | None = None
    scenario: Literal["ready", "empty", "partial", "error"] = "ready"
    stock_mode: Literal["set", "add"] = "set"


class DraftRequest(StrictDTO):
    plan_id: str
    plan_hash: str


class ReadPreviewRequest(PreviewRequest):
    role: Literal["member", "candidate"] = "member"
    auto_add: bool = False
    auto_add_date: str | None = None


class PreviewRow(StrictDTO):
    id: str
    offer_id: str
    name: str
    before: str | None
    after: str | None
    diff: str | None
    error: str | None
    currency: str | None = "RUB"


class Preview(StrictDTO):
    id: str
    hash: str
    kind: Literal["prices", "stocks"]
    created_at: str
    complete: bool
    valid: bool
    source: PreviewSource
    rows: list[PreviewRow]
    errors: list[str]


class Draft(StrictDTO):
    id: str
    status: Literal["DRAFT"] = "DRAFT"
    mode: Literal["MOCK"] = "MOCK"
    created_at: str
    plan: Preview
