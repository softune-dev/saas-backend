-- 011: footer becomes fully editable — description + two link columns.
--
-- footerText/footerLink were never read by any Aurora component (Footer.tsx
-- hardcoded its own description and both columns' links/labels, and worse,
-- the hardcoded Shop links were leftover copy from a different template —
-- "Sarees", "Panjabi", categories that don't exist in Aurora's own catalog).
-- Replaced with fields that are actually wired up:
--   footerDescription                text
--   footerShopLabel / footerShopLinks       jsonb array of {id,label,path}
--   footerCompanyLabel / footerCompanyLinks jsonb array of {id,label,path}
--
-- Newsletter column is intentionally NOT configurable — no fields for it by
-- design (see theme-context.tsx / editor-sidebar.tsx footer panel).
--
-- Scoped to Aurora only, same reasoning as migrations 009/010: other
-- templates may still use footerText/footerLink for their own real purpose.

UPDATE templates
SET default_theme =
      (default_theme - 'footerText' - 'footerLink')
      || jsonb_build_object(
           'footerDescription',
           'Quiet, considered handcrafted garments and artisanal goods designed for a slower pace. Made to last across generations.',
           'footerShopLabel', 'Shop',
           'footerShopLinks', '[
             {"id": "fs1", "label": "Women", "path": "/shop?category=women"},
             {"id": "fs2", "label": "Men", "path": "/shop?category=men"},
             {"id": "fs3", "label": "Bags", "path": "/shop?category=bags"},
             {"id": "fs4", "label": "Accessories", "path": "/shop?category=accessories"}
           ]'::jsonb,
           'footerCompanyLabel', 'Company',
           'footerCompanyLinks', '[
             {"id": "fc1", "label": "About", "path": "/about"},
             {"id": "fc2", "label": "Collections", "path": "/categories"},
             {"id": "fc3", "label": "FAQ", "path": "/faq"},
             {"id": "fc4", "label": "Contact", "path": "/contact"}
           ]'::jsonb
         )
WHERE key = 'aurora';

UPDATE sites
SET theme =
      (theme - 'footerText' - 'footerLink')
      || jsonb_build_object(
           'footerDescription',
           COALESCE(
             theme ->> 'footerDescription',
             'Quiet, considered handcrafted garments and artisanal goods designed for a slower pace. Made to last across generations.'
           ),
           'footerShopLabel', COALESCE(theme ->> 'footerShopLabel', 'Shop'),
           'footerShopLinks',
           COALESCE(theme -> 'footerShopLinks', '[
             {"id": "fs1", "label": "Women", "path": "/shop?category=women"},
             {"id": "fs2", "label": "Men", "path": "/shop?category=men"},
             {"id": "fs3", "label": "Bags", "path": "/shop?category=bags"},
             {"id": "fs4", "label": "Accessories", "path": "/shop?category=accessories"}
           ]'::jsonb),
           'footerCompanyLabel', COALESCE(theme ->> 'footerCompanyLabel', 'Company'),
           'footerCompanyLinks',
           COALESCE(theme -> 'footerCompanyLinks', '[
             {"id": "fc1", "label": "About", "path": "/about"},
             {"id": "fc2", "label": "Collections", "path": "/categories"},
             {"id": "fc3", "label": "FAQ", "path": "/faq"},
             {"id": "fc4", "label": "Contact", "path": "/contact"}
           ]'::jsonb)
         )
WHERE template_id IN (SELECT id FROM templates WHERE key = 'aurora')
  AND (
        theme ?| array['footerText', 'footerLink']
     OR NOT (theme ? 'footerDescription')
     OR NOT (theme ? 'footerShopLinks')
     OR NOT (theme ? 'footerCompanyLinks')
      );
