from contextlib import closing

import pymysql

from t2s.settings import Settings


def connect_mysql(settings: Settings):
    return pymysql.connect(
        host=settings.mysql_host,
        port=settings.mysql_port,
        database=settings.mysql_database,
        user=settings.mysql_user,
        password=settings.mysql_password.get_secret_value(),
        connect_timeout=2,
        read_timeout=2,
        write_timeout=2,
    )


def check_mysql(settings: Settings) -> None:
    """用应用账号验证认证、选库和课程表的读取权限。"""
    with closing(connect_mysql(settings)) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT id FROM orders LIMIT 1")
        cursor.fetchone()
