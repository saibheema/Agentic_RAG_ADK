CREATE TABLE IF NOT EXISTS customers (
  customer_id SERIAL PRIMARY KEY,
  full_name TEXT NOT NULL,
  email TEXT UNIQUE NOT NULL,
  region TEXT NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS products (
  product_id SERIAL PRIMARY KEY,
  sku TEXT UNIQUE NOT NULL,
  product_name TEXT NOT NULL,
  category TEXT NOT NULL,
  unit_price NUMERIC(10, 2) NOT NULL CHECK (unit_price > 0)
);

CREATE TABLE IF NOT EXISTS orders (
  order_id SERIAL PRIMARY KEY,
  customer_id INT NOT NULL REFERENCES customers(customer_id),
  order_date TIMESTAMP NOT NULL DEFAULT NOW(),
  status TEXT NOT NULL,
  total_amount NUMERIC(12, 2) NOT NULL CHECK (total_amount >= 0)
);

CREATE TABLE IF NOT EXISTS order_items (
  order_item_id SERIAL PRIMARY KEY,
  order_id INT NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
  product_id INT NOT NULL REFERENCES products(product_id),
  quantity INT NOT NULL CHECK (quantity > 0),
  line_total NUMERIC(12, 2) NOT NULL CHECK (line_total >= 0)
);

CREATE TABLE IF NOT EXISTS policy_targets (
  policy_target_id SERIAL PRIMARY KEY,
  metric_name TEXT NOT NULL,
  target_value NUMERIC(12, 2) NOT NULL,
  period_label TEXT NOT NULL,
  source_doc TEXT NOT NULL
);

TRUNCATE TABLE order_items, orders, products, customers, policy_targets RESTART IDENTITY CASCADE;

INSERT INTO customers (full_name, email, region) VALUES
  ('Ava Johnson', 'ava.johnson@example.com', 'North'),
  ('Liam Patel', 'liam.patel@example.com', 'South'),
  ('Noah Chen', 'noah.chen@example.com', 'West'),
  ('Emma Garcia', 'emma.garcia@example.com', 'East'),
  ('Olivia Brown', 'olivia.brown@example.com', 'North');

INSERT INTO products (sku, product_name, category, unit_price) VALUES
  ('SKU-1001', 'Pro Analytics License', 'Software', 199.00),
  ('SKU-1002', 'Data Integration Pack', 'Software', 499.00),
  ('SKU-2001', 'Onboarding Workshop', 'Services', 1200.00),
  ('SKU-3001', 'Priority Support Plan', 'Services', 799.00),
  ('SKU-4001', 'Security Add-on', 'Software', 299.00);

INSERT INTO orders (customer_id, order_date, status, total_amount) VALUES
  (1, NOW() - INTERVAL '40 days', 'completed', 1398.00),
  (2, NOW() - INTERVAL '31 days', 'completed', 499.00),
  (3, NOW() - INTERVAL '24 days', 'completed', 1998.00),
  (4, NOW() - INTERVAL '19 days', 'completed', 299.00),
  (5, NOW() - INTERVAL '15 days', 'completed', 2996.00),
  (1, NOW() - INTERVAL '9 days', 'completed', 798.00),
  (2, NOW() - INTERVAL '7 days', 'completed', 1598.00),
  (3, NOW() - INTERVAL '4 days', 'processing', 699.00),
  (4, NOW() - INTERVAL '3 days', 'completed', 999.00),
  (5, NOW() - INTERVAL '1 days', 'completed', 1798.00);

INSERT INTO order_items (order_id, product_id, quantity, line_total) VALUES
  (1, 1, 3, 597.00), (1, 3, 1, 1200.00),
  (2, 2, 1, 499.00),
  (3, 2, 2, 998.00), (3, 3, 1, 1200.00),
  (4, 5, 1, 299.00),
  (5, 2, 4, 1996.00), (5, 5, 2, 598.00),
  (6, 1, 1, 199.00), (6, 4, 1, 799.00),
  (7, 3, 1, 1200.00), (7, 5, 1, 299.00),
  (8, 1, 2, 398.00), (8, 5, 1, 299.00),
  (9, 4, 1, 799.00), (9, 1, 1, 199.00),
  (10, 3, 1, 1200.00), (10, 4, 1, 799.00);

INSERT INTO policy_targets (metric_name, target_value, period_label, source_doc) VALUES
  ('q4_sales_target', 12000.00, 'Q4', 'contracts/2026-sales-targets.pdf'),
  ('avg_order_value_target', 1000.00, 'Q4', 'contracts/2026-sales-targets.pdf'),
  ('north_region_target', 3500.00, 'Q4', 'contracts/2026-regional-targets.pdf');
