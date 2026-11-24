from contextlib import closing
from datetime import date
from decimal import Decimal

import pymysql

from t2s.infrastructure.mysql import connect_mysql
from t2s.query.compiler import CompiledQuery
from t2s.settings import Settings

Rows = list[list[str | int | None]]


class QueryExecutionError(RuntimeError):
    """可展示给业务调用方的查询失败。"""


def _cell(value: object) -> str | int | None:
    if isinstance(value, Decimal) and value == value.to_integral_value():
        return int(value)
    if isinstance(value, date):
        return value.isoformat()
    if value is None or isinstance(value, (str, int)):
        return value
    raise QueryExecutionError("课程查询返回了不支持的数据类型")


def execute_query(query: CompiledQuery, settings: Settings) -> Rows:
    try:
        with closing(connect_mysql(settings)) as connection, connection.cursor() as cursor:
            cursor.execute("SET SESSION MAX_EXECUTION_TIME=1500")
            cursor.execute("START TRANSACTION READ ONLY")
            try:
                cursor.execute(query.text, query.params)
                rows = cursor.fetchmany(1001)
                if len(rows) > 1000:
                    raise QueryExecutionError("结果超过1000行，请缩小范围")
                return [[_cell(value) for value in row] for row in rows]
            finally:
                connection.rollback()
    except pymysql.MySQLError as error:
        raise QueryExecutionError("数据库查询失败，请查看服务日志") from error
