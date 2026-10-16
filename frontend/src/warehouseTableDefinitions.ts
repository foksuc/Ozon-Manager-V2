export const warehouseTableDefinitions={
  "warehouse-category_tree": {
    "label": "Склад · Категории товаров",
    "keys": [
      "description_category_id",
      "category_name",
      "children",
      "disabled",
      "type_id",
      "type_name"
    ]
  },
  "warehouse-category_attributes": {
    "label": "Склад · Характеристики категории",
    "keys": [
      "category_dependent",
      "description",
      "dictionary_id",
      "group_id",
      "group_name",
      "id",
      "is_aspect",
      "is_collection",
      "is_required",
      "name",
      "type",
      "attribute_complex_id",
      "max_value_count",
      "complex_is_collection"
    ]
  },
  "warehouse-category_values": {
    "label": "Склад · Значения характеристики",
    "keys": [
      "id",
      "info",
      "picture",
      "value"
    ]
  },
  "warehouse-category_search": {
    "label": "Склад · Поиск значений характеристики",
    "keys": [
      "id",
      "info",
      "picture",
      "value"
    ]
  },
  "warehouse-product_import_info": {
    "label": "Склад · Задачи импорта",
    "keys": [
      "offer_id",
      "product_id",
      "status",
      "errors"
    ]
  },
  "warehouse-product_attributes": {
    "label": "Склад · Характеристики товаров",
    "keys": [
      "attributes",
      "attributes_with_defaults",
      "barcode",
      "barcodes",
      "description_category_id",
      "color_image",
      "complex_attributes",
      "depth",
      "dimension_unit",
      "height",
      "id",
      "images",
      "model_info",
      "name",
      "offer_id",
      "pdf_list",
      "primary_image",
      "sku",
      "type_id",
      "weight",
      "weight_unit",
      "width"
    ]
  },
  "warehouse-product_limits": {
    "label": "Склад · Лимиты товаров",
    "keys": [
      "daily_create",
      "daily_update",
      "operation_limits",
      "total"
    ]
  },
  "warehouse-product_description": {
    "label": "Склад · Описание товара",
    "keys": [
      "result"
    ]
  },
  "warehouse-product_pictures": {
    "label": "Склад · Изображения товаров",
    "keys": [
      "product_id",
      "primary_photo",
      "photo",
      "color_photo",
      "errors"
    ]
  },
  "warehouse-price_timer_status": {
    "label": "Цены · Таймеры",
    "keys": [
      "expired_at",
      "min_price_for_auto_actions_enabled",
      "product_id"
    ]
  },
  "warehouse-price_discounted": {
    "label": "Цены · Уценённые товары",
    "keys": [
      "comment_reason_damaged",
      "condition",
      "condition_estimation",
      "defects",
      "discounted_sku",
      "mechanical_damage",
      "package_damage",
      "packaging_violation",
      "reason_damaged",
      "repair",
      "shortage",
      "sku",
      "warranty_type"
    ]
  },
  "warehouse-report_list": {
    "label": "Отчёты · Список",
    "keys": [
      "additional_data",
      "code",
      "created_at",
      "error",
      "expires_at",
      "file",
      "params",
      "report_type",
      "status"
    ]
  },
  "warehouse-report_info": {
    "label": "Отчёты · Информация",
    "keys": [
      "key",
      "value"
    ]
  },
  "warehouse-product_list": {
    "label": "Склад · Каталог товаров",
    "keys": [
      "archived",
      "has_fbo_stocks",
      "has_fbs_stocks",
      "is_discounted",
      "offer_id",
      "product_id",
      "sku",
      "quants"
    ]
  },
  "warehouse-product_info": {
    "label": "Склад · Карточки товаров",
    "keys": [
      "availabilities",
      "barcodes",
      "color_image",
      "commissions",
      "created_at",
      "currency_code",
      "declared_price",
      "description_category_id",
      "discounted_fbo_stocks",
      "errors",
      "has_discounted_fbo_item",
      "id",
      "images",
      "is_archived",
      "is_autoarchived",
      "is_discounted",
      "is_kgt",
      "is_prepayment_allowed",
      "is_super",
      "min_price",
      "model_info",
      "name",
      "offer_id",
      "old_price",
      "price",
      "price_indexes",
      "primary_image",
      "promotions",
      "sku",
      "sources",
      "statuses",
      "stocks",
      "type_id",
      "updated_at",
      "vat",
      "visibility_details",
      "volume_weight"
    ]
  },
  "warehouse-product_prices": {
    "label": "Цены · Цены товаров",
    "keys": [
      "acquiring",
      "commissions",
      "marketing_actions",
      "offer_id",
      "price",
      "price_indexes",
      "product_id",
      "volume_weight"
    ]
  },
  "warehouse-category_dependent": {
    "label": "Склад · Зависимые характеристики",
    "keys": [
      "child_attribute_id",
      "parent_attribute_id"
    ]
  },
  "warehouse-category_dependent_values": {
    "label": "Склад · Значения зависимых характеристик",
    "keys": [
      "children",
      "parent_value",
      "parent_value_id"
    ]
  }
} as const
