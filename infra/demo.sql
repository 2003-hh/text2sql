-- 独立 Quick Start 用的虚构数据。MySQL 镜像在新数据卷初始化时执行。
USE text2sql_course;
SET NAMES utf8mb4;

CREATE TABLE orders (
    id BIGINT PRIMARY KEY,
    status VARCHAR(16) NOT NULL,
    paid_at DATETIME,
    paid_cents INT NOT NULL
);
CREATE TABLE products (id BIGINT PRIMARY KEY, category VARCHAR(40) NOT NULL);
CREATE TABLE order_items (
    order_id BIGINT NOT NULL,
    product_id BIGINT NOT NULL,
    paid_cents INT NOT NULL,
    PRIMARY KEY (order_id, product_id)
);
CREATE TABLE refunds (
    id BIGINT PRIMARY KEY,
    order_id BIGINT NOT NULL,
    status VARCHAR(16) NOT NULL,
    amount_cents INT NOT NULL
);
CREATE TABLE shipments (
    id BIGINT PRIMARY KEY,
    order_id BIGINT NOT NULL,
    status VARCHAR(16) NOT NULL,
    shipped_at DATETIME
);
CREATE TABLE inventory_balances (
    product_id BIGINT NOT NULL,
    snapshot_date DATE NOT NULL,
    on_hand INT NOT NULL,
    PRIMARY KEY (product_id, snapshot_date)
);

INSERT INTO products VALUES (1, '键盘'), (2, '耳机'), (3, '电脑');
INSERT INTO orders VALUES
    (1, 'paid', '2026-07-01 10:00:00', 16000),
    (2, 'paid', '2026-08-05 10:00:00', 18000),
    (3, 'paid', '2026-09-14 15:00:00', 9900),
    (4, 'cancelled', NULL, 0),
    (5, 'paid', '2026-10-01 00:00:00', 19900);
INSERT INTO orders (id, status, paid_at, paid_cents)
WITH RECURSIVE days AS (
    SELECT 0 AS day_offset
    UNION ALL SELECT day_offset + 1 FROM days WHERE day_offset < 29
)
SELECT 100 + day_offset, 'paid', DATE_ADD('2026-09-01 12:00:00', INTERVAL day_offset DAY),
       12000 + day_offset
FROM days;

INSERT INTO order_items VALUES (1, 1, 16000), (2, 2, 18000), (3, 2, 9900), (5, 3, 19900);
INSERT INTO order_items SELECT id, 1, paid_cents FROM orders WHERE id >= 100;
INSERT INTO refunds VALUES (1, 100, 'completed', 1500), (2, 100, 'pending', 700);
INSERT INTO shipments VALUES
    (1, 100, 'shipped', '2026-09-02 10:00:00'),
    (2, 100, 'delivered', '2026-09-03 10:00:00');
INSERT INTO inventory_balances VALUES
    (1, '2026-10-31', 25), (2, '2026-10-31', 40), (3, '2026-10-31', 12);

-- 镜像先为 MYSQL_USER 授予库级权限，再运行本文件。将应用账号收窄为只读。
REVOKE ALL PRIVILEGES, GRANT OPTION FROM 'course_reader'@'%';
GRANT SELECT ON text2sql_course.* TO 'course_reader'@'%';
