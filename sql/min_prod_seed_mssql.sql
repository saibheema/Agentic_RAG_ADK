-- SQL Server (T-SQL) seed script — equivalent of min_prod_seed.sql for PostgreSQL.
-- Run with: sqlcmd -S HOST -U USER -P PASSWORD -d DATABASE -i sql/min_prod_seed_mssql.sql

IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'customers')
CREATE TABLE customers (
  customer_id INT IDENTITY(1,1) PRIMARY KEY,
  full_name NVARCHAR(255) NOT NULL,
  email NVARCHAR(255) NOT NULL UNIQUE,
  region NVARCHAR(100) NOT NULL,
  created_at DATETIME2 NOT NULL DEFAULT GETDATE()
);

IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'products')
CREATE TABLE products (
  product_id INT IDENTITY(1,1) PRIMARY KEY,
  sku NVARCHAR(50) NOT NULL UNIQUE,
  product_name NVARCHAR(255) NOT NULL,
  category NVARCHAR(100) NOT NULL,
  unit_price DECIMAL(10, 2) NOT NULL CHECK (unit_price > 0)
);

IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'orders')
CREATE TABLE orders (
  order_id INT IDENTITY(1,1) PRIMARY KEY,
  customer_id INT NOT NULL REFERENCES customers(customer_id),
  order_date DATETIME2 NOT NULL DEFAULT GETDATE(),
  status NVARCHAR(50) NOT NULL,
  total_amount DECIMAL(12, 2) NOT NULL CHECK (total_amount >= 0)
);

IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'order_items')
CREATE TABLE order_items (
  order_item_id INT IDENTITY(1,1) PRIMARY KEY,
  order_id INT NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
  product_id INT NOT NULL REFERENCES products(product_id),
  quantity INT NOT NULL CHECK (quantity > 0),
  line_total DECIMAL(12, 2) NOT NULL CHECK (line_total >= 0)
);

IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'policy_targets')
CREATE TABLE policy_targets (
  policy_target_id INT IDENTITY(1,1) PRIMARY KEY,
  metric_name NVARCHAR(255) NOT NULL,
  target_value DECIMAL(12, 2) NOT NULL,
  period_label NVARCHAR(50) NOT NULL,
  source_doc NVARCHAR(500) NOT NULL
);

-- Clear existing data (order matters for FK constraints)
DELETE FROM order_items;
DELETE FROM orders;
DELETE FROM products;
DELETE FROM customers;
DELETE FROM policy_targets;

-- Reset identity columns
DBCC CHECKIDENT ('customers', RESEED, 0);
DBCC CHECKIDENT ('products', RESEED, 0);
DBCC CHECKIDENT ('orders', RESEED, 0);
DBCC CHECKIDENT ('order_items', RESEED, 0);
DBCC CHECKIDENT ('policy_targets', RESEED, 0);

SET IDENTITY_INSERT customers ON;
INSERT INTO customers (customer_id, full_name, email, region) VALUES
  (1, N'Ava Johnson', N'ava.johnson@example.com', N'North'),
  (2, N'Liam Patel', N'liam.patel@example.com', N'South'),
  (3, N'Noah Chen', N'noah.chen@example.com', N'West'),
  (4, N'Emma Garcia', N'emma.garcia@example.com', N'East'),
  (5, N'Olivia Brown', N'olivia.brown@example.com', N'North');
SET IDENTITY_INSERT customers OFF;

SET IDENTITY_INSERT products ON;
INSERT INTO products (product_id, sku, product_name, category, unit_price) VALUES
  (1, N'SKU-1001', N'Pro Analytics License', N'Software', 199.00),
  (2, N'SKU-1002', N'Data Integration Pack', N'Software', 499.00),
  (3, N'SKU-2001', N'Onboarding Workshop', N'Services', 1200.00),
  (4, N'SKU-3001', N'Priority Support Plan', N'Services', 799.00),
  (5, N'SKU-4001', N'Security Add-on', N'Software', 299.00);
SET IDENTITY_INSERT products OFF;

SET IDENTITY_INSERT orders ON;
INSERT INTO orders (order_id, customer_id, order_date, status, total_amount) VALUES
  (1, 1, DATEADD(DAY, -40, GETDATE()), N'completed', 1398.00),
  (2, 2, DATEADD(DAY, -31, GETDATE()), N'completed', 499.00),
  (3, 3, DATEADD(DAY, -24, GETDATE()), N'completed', 1998.00),
  (4, 4, DATEADD(DAY, -19, GETDATE()), N'completed', 299.00),
  (5, 5, DATEADD(DAY, -15, GETDATE()), N'completed', 2996.00),
  (6, 1, DATEADD(DAY, -9, GETDATE()), N'completed', 798.00),
  (7, 2, DATEADD(DAY, -7, GETDATE()), N'completed', 1598.00),
  (8, 3, DATEADD(DAY, -4, GETDATE()), N'processing', 699.00),
  (9, 4, DATEADD(DAY, -3, GETDATE()), N'completed', 999.00),
  (10, 5, DATEADD(DAY, -1, GETDATE()), N'completed', 1798.00);
SET IDENTITY_INSERT orders OFF;

SET IDENTITY_INSERT order_items ON;
INSERT INTO order_items (order_item_id, order_id, product_id, quantity, line_total) VALUES
  (1, 1, 1, 3, 597.00), (2, 1, 3, 1, 1200.00),
  (3, 2, 2, 1, 499.00),
  (4, 3, 2, 2, 998.00), (5, 3, 3, 1, 1200.00),
  (6, 4, 5, 1, 299.00),
  (7, 5, 2, 4, 1996.00), (8, 5, 5, 2, 598.00),
  (9, 6, 1, 1, 199.00), (10, 6, 4, 1, 799.00),
  (11, 7, 3, 1, 1200.00), (12, 7, 5, 1, 299.00),
  (13, 8, 1, 2, 398.00), (14, 8, 5, 1, 299.00),
  (15, 9, 4, 1, 799.00), (16, 9, 1, 1, 199.00),
  (17, 10, 3, 1, 1200.00), (18, 10, 4, 1, 799.00);
SET IDENTITY_INSERT order_items OFF;

SET IDENTITY_INSERT policy_targets ON;
INSERT INTO policy_targets (policy_target_id, metric_name, target_value, period_label, source_doc) VALUES
  (1, N'q4_sales_target', 12000.00, N'Q4', N'contracts/2026-sales-targets.pdf'),
  (2, N'avg_order_value_target', 1000.00, N'Q4', N'contracts/2026-sales-targets.pdf'),
  (3, N'north_region_target', 3500.00, N'Q4', N'contracts/2026-regional-targets.pdf');
SET IDENTITY_INSERT policy_targets OFF;
