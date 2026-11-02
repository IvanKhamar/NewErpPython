"""tools/check_live.py: what the reconstruction declares and how it is compared. The real run needs the owner's access;
here the schema check runs against the test database, built from the same files, so it must find no difference."""

import asyncio

from tests.conftest import SERVER_URL
from tools.check_live import RECONSTRUCTED, Column, check_schema, compare, expected_columns

SQL = """
CREATE TABLE `dd_thing` (
  `thing_id` int(11) NOT NULL AUTO_INCREMENT,
  `name` varchar(64) NOT NULL DEFAULT '',
  PRIMARY KEY (`thing_id`)
) ENGINE=MyISAM;

ALTER TABLE `dd_order`
  ADD COLUMN `instagram` varchar(64) NOT NULL DEFAULT '',
  ADD COLUMN `callback` tinyint(1) NOT NULL DEFAULT '0';
"""


def test_reads_created_tables_and_added_columns():
    assert expected_columns(SQL) == [
        Column("dd_thing", "thing_id", "int"),
        Column("dd_thing", "name", "varchar(64)"),
        Column("dd_order", "instagram", "varchar(64)"),
        Column("dd_order", "callback", "tinyint"),
    ]
    assert len(expected_columns(RECONSTRUCTED.read_text(encoding="utf-8"))) > 100


def test_compare_names_what_is_missing_or_different():
    expected = expected_columns(SQL)
    actual = {("dd_order", "instagram"): "varchar(32)", ("dd_order", "other"): "int"}
    assert compare(expected, actual) == [
        "немає таблиці dd_thing",
        "dd_order.instagram: у нас varchar(64), у базі varchar(32)",
        "dd_order: немає колонки callback (tinyint)",
    ]


def test_the_test_database_matches_the_reconstruction(database):
    url = SERVER_URL.set(database=database, query={"charset": "utf8mb4"}).render_as_string(hide_password=False)
    assert asyncio.run(check_schema(url)) == []
