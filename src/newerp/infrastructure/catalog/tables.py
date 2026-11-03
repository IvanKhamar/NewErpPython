"""OpenCart catalog reference tables the ERP reads and writes without ORM classes (Core tables, like Dapper
queries next to EF entities): filters, attributes, options, manufacturers, downloads and their product links."""

from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, Text

metadata = MetaData()


def _names(name: str, key: str, *extra: Column) -> Table:
    return Table(
        name, metadata, Column(key, Integer, primary_key=True), Column("language_id", Integer, primary_key=True),
        Column("name", String(128)), *extra,
    )  # fmt: skip


filter_group = Table(
    "dd_filter_group", metadata, Column("filter_group_id", Integer, primary_key=True), Column("sort_order", Integer)
)
filter_group_description = _names("dd_filter_group_description", "filter_group_id")
attribute_group = Table(
    "dd_attribute_group",
    metadata,
    Column("attribute_group_id", Integer, primary_key=True),
    Column("sort_order", Integer),
)
attribute_group_description = _names("dd_attribute_group_description", "attribute_group_id")
attribute = Table(
    "dd_attribute", metadata, Column("attribute_id", Integer, primary_key=True), Column("attribute_group_id", Integer),
    Column("sort_order", Integer),
)  # fmt: skip
attribute_description = _names("dd_attribute_description", "attribute_id")
product_attribute = Table(
    "dd_product_attribute", metadata, Column("product_id", Integer, primary_key=True),
    Column("attribute_id", Integer, primary_key=True), Column("language_id", Integer, primary_key=True),
    Column("text", Text),
)  # fmt: skip
option = Table(
    "dd_option", metadata, Column("option_id", Integer, primary_key=True), Column("type", String(32)),
    Column("sort_order", Integer),
)  # fmt: skip
option_value = Table(
    "dd_option_value", metadata, Column("option_value_id", Integer, primary_key=True), Column("option_id", Integer),
    Column("image", String(255)), Column("sort_order", Integer),
)  # fmt: skip
manufacturer = Table(
    "dd_manufacturer", metadata, Column("manufacturer_id", Integer, primary_key=True), Column("name", String(64)),
    Column("image", String(255)), Column("sort_order", Integer),
)  # fmt: skip
manufacturer_to_store = Table(
    "dd_manufacturer_to_store", metadata, Column("manufacturer_id", Integer, primary_key=True),
    Column("store_id", Integer, primary_key=True),
)  # fmt: skip
download = Table(
    "dd_download", metadata, Column("download_id", Integer, primary_key=True), Column("filename", String(160)),
    Column("mask", String(128)), Column("date_added", DateTime),
)  # fmt: skip
download_description = _names("dd_download_description", "download_id")
product_to_download = Table(
    "dd_product_to_download", metadata, Column("product_id", Integer, primary_key=True),
    Column("download_id", Integer, primary_key=True),
)  # fmt: skip
