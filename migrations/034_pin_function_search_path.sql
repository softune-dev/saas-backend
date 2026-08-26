-- Supabase Security Advisor: "Function Search Path Mutable" on set_updated_at.
-- Without a pinned search_path, this SECURITY DEFINER-adjacent trigger function
-- resolves unqualified names via the caller's search_path, which could be
-- hijacked by an object created earlier in that path. Pin it to empty/public
-- so it always resolves against known schemas regardless of caller.
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = pg_catalog, public
AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;
