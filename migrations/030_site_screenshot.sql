-- Mobile-viewport storefront screenshot, captured by the worker after each
-- publish (queue.JOB_CAPTURE_SCREENSHOT) and shown on the Themes page card.
alter table sites
  add column if not exists screenshot_url text null;
