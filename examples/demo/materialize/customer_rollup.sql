SELECT
    u.id                           AS customer_id,
    u.email                        AS email,
    COUNT(DISTINCT o.id)           AS order_count,
    COALESCE(SUM(p.amount_cents), 0) AS lifetime_value_cents,
    MAX(o.created_at)              AS last_order_at
FROM public.users u
LEFT JOIN public.orders   o ON o.user_id  = u.id
LEFT JOIN public.payments p ON p.order_id = o.id
GROUP BY u.id, u.email;
