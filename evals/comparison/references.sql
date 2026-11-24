-- P01 按发货日期统计已发货或已送达订单，订单去重。
SELECT DATE(shipped_at), COUNT(DISTINCT order_id)
FROM shipments
WHERE status IN ('shipped','delivered') AND shipped_at >= '2026-09-01' AND shipped_at < '2026-10-01'
GROUP BY DATE(shipped_at) ORDER BY DATE(shipped_at);

-- P02 用相关子查询分别计算每单全部已完成退款，含跨月，再按支付日汇总。
SELECT DATE(o.paid_at),
       SUM(o.paid_cents - COALESCE(
           (SELECT SUM(r.amount_cents) FROM refunds r WHERE r.order_id = o.id AND r.status = 'completed'),
           0
       ))
FROM orders o
WHERE o.status = 'paid' AND o.paid_at >= '2026-09-01' AND o.paid_at < '2026-10-01'
GROUP BY DATE(o.paid_at) ORDER BY DATE(o.paid_at);
