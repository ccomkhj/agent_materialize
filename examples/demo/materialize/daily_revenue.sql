SELECT
    date_trunc('day', p.paid_at)::date AS day,
    pr.id                              AS product_id,
    pr.sku                             AS sku,
    SUM(oi.qty)                        AS units_sold,
    SUM(oi.qty * pr.price_cents)       AS gross_revenue_cents
FROM public.payments    p
JOIN public.orders      o  ON o.id  = p.order_id
JOIN public.order_items oi ON oi.order_id = o.id
JOIN public.products    pr ON pr.id = oi.product_id
WHERE p.paid_at >= now() - interval '90 days'
GROUP BY 1, 2, 3;
