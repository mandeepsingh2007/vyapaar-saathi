-- Create the 'transactions' table
CREATE TABLE transactions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    transaction_date DATE NOT NULL,
    transaction_type VARCHAR(50) NOT NULL, -- 'sale' or 'expense'
    amount NUMERIC(10, 2) NOT NULL,
    item TEXT,
    user_id TEXT NOT NULL, -- New: To associate transactions with a specific shopkeeper (WhatsApp sender_id)
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Optional: Add RLS policies for security
-- For example, allowing users to only see their own transactions
ALTER TABLE transactions ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view their own transactions." ON transactions
  FOR SELECT USING (auth.uid() = user_id); -- Assuming a 'user_id' column for linking to auth.users

CREATE POLICY "Users can insert their own transactions." ON transactions
  FOR INSERT WITH CHECK (auth.uid() = user_id);

-- Note: You would need to add a 'user_id' column to the 'transactions' table
-- and link it to Supabase's auth.users table if you implement RLS.
-- For simplicity, this example does not include the 'user_id' column in the initial table creation.
-- If you plan to use RLS, uncomment and add the user_id column:
-- ALTER TABLE transactions
-- ADD COLUMN user_id UUID REFERENCES auth.users(id) DEFAULT auth.uid();


CREATE TABLE stock_items (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    item_name TEXT NOT NULL,
    quantity NUMERIC(10, 2) NOT NULL DEFAULT 0, -- Changed to NUMERIC to allow for fractional values
    unit TEXT, -- New: To store units like kg, dozen, etc.
    num_packets INTEGER NOT NULL DEFAULT 1, -- New: To store the number of packets or separate count
    cost_price_per_unit NUMERIC(10, 2), -- New: To store the cost price per unit of the item
    last_updated TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    CONSTRAINT unique_user_item UNIQUE (user_id, item_name, unit)
);
