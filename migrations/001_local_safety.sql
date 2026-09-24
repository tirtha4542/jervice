-- Optional local safety migration for an existing PostgreSQL database.
-- Review before applying. The application does not run this file automatically.

CREATE UNIQUE INDEX IF NOT EXISTS uq_table_sessions_active_table
    ON table_sessions (table_id)
    WHERE status <> 'closed';

CREATE UNIQUE INDEX IF NOT EXISTS uq_recipe_menu_sku
    ON recipe_components (menu_item_id, sku_id);

ALTER TABLE dining_tables
    ADD CONSTRAINT ck_dining_table_capacity_positive CHECK (capacity > 0);
ALTER TABLE inventory_skus
    ADD CONSTRAINT ck_inventory_on_hand_nonnegative CHECK (on_hand >= 0),
    ADD CONSTRAINT ck_inventory_par_nonnegative CHECK (par_level >= 0);
ALTER TABLE recipe_components
    ADD CONSTRAINT ck_recipe_quantity_positive CHECK (quantity > 0);
ALTER TABLE order_items
    ADD CONSTRAINT ck_order_item_quantity_positive CHECK (quantity > 0);
ALTER TABLE payments
    ADD CONSTRAINT ck_payment_amount_positive CHECK (amount > 0);
ALTER TABLE reservations
    ADD CONSTRAINT ck_reservation_party_size_positive CHECK (party_size > 0);
