-- Tiny e-commerce schema. Five tables, ~50 rows total. Just enough to exercise
-- joins, aggregations, MV-on-MV lineage, and the access boundary.

CREATE TABLE public.users (
    id          SERIAL PRIMARY KEY,
    email       TEXT NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE public.products (
    id          SERIAL PRIMARY KEY,
    sku         TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    price_cents INTEGER NOT NULL CHECK (price_cents >= 0)
);

CREATE TABLE public.orders (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES public.users(id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE public.order_items (
    order_id    INTEGER NOT NULL REFERENCES public.orders(id),
    product_id  INTEGER NOT NULL REFERENCES public.products(id),
    qty         INTEGER NOT NULL CHECK (qty > 0),
    PRIMARY KEY (order_id, product_id)
);

CREATE TABLE public.payments (
    id          SERIAL PRIMARY KEY,
    order_id    INTEGER NOT NULL REFERENCES public.orders(id),
    amount_cents INTEGER NOT NULL,
    paid_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO public.users (email, created_at) VALUES
    ('alice@example.com',   now() - interval '120 days'),
    ('bob@example.com',     now() - interval '90 days'),
    ('carol@example.com',   now() - interval '60 days'),
    ('dave@example.com',    now() - interval '20 days'),
    ('eve@example.com',     now() - interval '5 days');

INSERT INTO public.products (sku, name, price_cents) VALUES
    ('SKU-001', 'Wireless Mouse',     2500),
    ('SKU-002', 'Mechanical Keyboard', 9900),
    ('SKU-003', 'USB-C Hub',          4500),
    ('SKU-004', 'Laptop Stand',       3500),
    ('SKU-005', 'Desk Mat',           2000);

-- Orders spread across the last 90 days so the 30-day window is meaningful.
INSERT INTO public.orders (user_id, created_at) VALUES
    (1, now() - interval '85 days'),
    (1, now() - interval '40 days'),
    (1, now() - interval '10 days'),
    (2, now() - interval '70 days'),
    (2, now() - interval '15 days'),
    (3, now() - interval '50 days'),
    (3, now() - interval '25 days'),
    (3, now() - interval '3 days'),
    (4, now() - interval '12 days'),
    (5, now() - interval '2 days');

INSERT INTO public.order_items (order_id, product_id, qty) VALUES
    (1, 1, 1), (1, 5, 2),
    (2, 2, 1),
    (3, 3, 1), (3, 4, 1),
    (4, 1, 2),
    (5, 2, 1), (5, 3, 1),
    (6, 4, 1),
    (7, 1, 1), (7, 5, 1),
    (8, 2, 1), (8, 3, 1), (8, 4, 1),
    (9, 5, 3),
    (10, 1, 1), (10, 2, 1);

-- One payment per order, amount = sum(qty * price). Done by hand so the seed
-- data is deterministic and humans can sanity-check the materialized views.
INSERT INTO public.payments (order_id, amount_cents, paid_at) VALUES
    (1,  2500 + 2*2000, now() - interval '85 days'),
    (2,  9900,          now() - interval '40 days'),
    (3,  4500 + 3500,   now() - interval '10 days'),
    (4,  2*2500,        now() - interval '70 days'),
    (5,  9900 + 4500,   now() - interval '15 days'),
    (6,  3500,          now() - interval '50 days'),
    (7,  2500 + 2000,   now() - interval '25 days'),
    (8,  9900 + 4500 + 3500, now() - interval '3 days'),
    (9,  3*2000,        now() - interval '12 days'),
    (10, 2500 + 9900,   now() - interval '2 days');
