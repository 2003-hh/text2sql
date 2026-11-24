-- R01：独立对照 9 月 10 日至 15 日的实付金额。
SELECT DATE(paid_at), SUM(paid_cents)
FROM orders
WHERE status = 'paid' AND paid_at >= '2026-09-10' AND paid_at < '2026-09-16'
GROUP BY DATE(paid_at)
ORDER BY DATE(paid_at);

-- R02：独立对照键盘实付金额，使用订单明细口径。
SELECT DATE(o.paid_at), SUM(i.paid_cents)
FROM orders o
JOIN order_items i ON i.order_id = o.id
WHERE o.status = 'paid' AND o.paid_at >= '2026-09-01' AND o.paid_at < '2026-10-01'
  AND i.product_id IN (SELECT p.id FROM products p WHERE p.category = '键盘')
GROUP BY DATE(o.paid_at)
ORDER BY DATE(o.paid_at);
