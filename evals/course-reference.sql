-- A01 实付金额按支付日汇总，金额单位分。
SELECT DATE(paid_at), SUM(paid_cents)
FROM orders
WHERE status = 'paid' AND paid_at >= '2026-09-01' AND paid_at < '2026-10-01'
GROUP BY DATE(paid_at) ORDER BY DATE(paid_at);

-- A02 成功支付订单按月计数。
SELECT DATE_FORMAT(paid_at, '%Y-%m'), COUNT(*)
FROM orders
WHERE status = 'paid' AND paid_at >= '2026-07-01' AND paid_at < '2026-10-01'
GROUP BY DATE_FORMAT(paid_at, '%Y-%m') ORDER BY DATE_FORMAT(paid_at, '%Y-%m');

-- A03 仅计算指定快照日的现存库存。
SELECT p.category, SUM(b.on_hand)
FROM inventory_balances b JOIN products p ON p.id = b.product_id
WHERE b.snapshot_date = '2026-10-31'
GROUP BY p.category ORDER BY p.category;
